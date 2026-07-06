# UI-019 — Smart Onboarding Flow

**Status:** ✅ Done  
**Area:** Frontend + Backend (one-line infer change)  
**Depends on:** UI-018 (dashboard handoff — done), BE-005 (whatsapp_enabled — done)

---

## Problem

The current onboarding asks every user the same 8 questions regardless of whether Swiggy order history exists. When history is available, PantryPilot can already infer diet type (from product names), budget (from median order total), and household size (from order volume). Asking these questions anyway makes the app feel generic and slow.

Additionally, Step 7 (basket preview) shows a read-only list of inferred items and calls it "your basket this week" — but it can't be edited and doesn't represent a real pending order. It's misleading and adds no value.

---

## Goals

1. **Skip the questionnaire when order history exists** — go straight to inference, then to All Set
2. **Show the questionnaire only when there is no history** — the planner needs explicit input to work
3. **Remove Step 7** — the first real, editable basket belongs on the dashboard
4. **Keep the step system extensible** — future steps can be added without renumbering or restructuring

---

## Backend change — `has_order_history` field

`GET /onboard/infer` must return a structured boolean field:

```python
# In InferenceResult.__init__  (onboarding_service.py):
self.has_order_history: bool = False

# In run_inference(), after fetching orders (onboarding_service.py):
result.has_order_history = len(orders) > 0

# In the API response (onboard.py):
"has_order_history": result.has_order_history,
```

**Why not string matching:** the original design proposed parsing `confidence_notes` for `"No past Instamart orders found"`. This is fragile — any edit to the backend message string silently breaks the frontend's branch logic. A structured boolean is the correct contract.

**Scope note:** `orders` is a local variable inside `run_inference()` in `onboarding_service.py` — it is not in scope at the API response construction point in `onboard.py`. The field must be set on `InferenceResult` inside the service (where `orders` is available), then read from `result` in the API handler. Two additions: one in the service (`InferenceResult` + assignment), one in the API response dict.

The frontend reads:
```typescript
const hasHistory = inferResult.has_order_history === true
```

**Failure fallback:** if `has_order_history` is missing or the infer call fails, default `hasHistory = false` — show the questionnaire. When uncertain, ask rather than skip. Never default to `true`.

---

## New flow design

### Flow A — history exists

| WA enabled | Steps |
|---|---|
| Yes | inference → phone → otp → allset (4 steps) |
| No  | inference → allset (2 steps) |

### Flow B — no history

| WA enabled | Steps |
|---|---|
| Yes | household → diet → budget → inference → phone → otp → allset (7 steps) |
| No  | household → diet → budget → inference → allset (5 steps) |

### Step bar

Only shown during questionnaire steps (household, diet, budget). Hidden for inference, WA, and All Set. Denominator is `questionnaireSteps.length` — never hardcoded.

```typescript
const questionnaireSteps = flow.filter(s => ["household", "diet", "budget"].includes(s))
const isQuestionnaireStep = questionnaireSteps.includes(currentStep)
const currentQStep = questionnaireSteps.indexOf(currentStep) + 1
// Render: {currentQStep} of {questionnaireSteps.length}
```

---

## Extensibility — flow-as-array

Instead of hardcoded `setStep(5)` calls, the flow is defined as an ordered array of step keys. Navigation is `goNext()` / `goBack()` which advance/retreat by index.

```typescript
type StepKey =
  | "household" | "diet" | "budget"   // questionnaire (Flow B only)
  | "inference"                         // always present
  | "phone" | "otp"                     // WA, conditional
  | "allset"                            // always present

function computeFlow(hasHistory: boolean, whatsappEnabled: boolean): StepKey[] {
  const questionnaire: StepKey[] = hasHistory ? [] : ["household", "diet", "budget"]
  const wa: StepKey[]            = whatsappEnabled ? ["phone", "otp"] : []
  return [...questionnaire, "inference", ...wa, "allset"]
}
```

Adding a new step in future: add its key to `StepKey`, add its component, insert it in `computeFlow`. No renumbering anywhere.

### `goBack()` behaviour

- When `currentStep === flow[0]`: hide the back button entirely — there is nowhere to go
- In Flow A, `flow[0] === "inference"`, so the back button is hidden on the inference step
- In Flow B, `flow[0] === "household"`, so the back button is hidden on step 1 only
- `goBack()` from `"inference"` in Flow B returns to `"budget"` — this is correct and desirable. Inference is idempotent so re-running it after the user edits their budget is safe.

---

## When `hasHistory` is known — bootstrapping

`hasHistory` is returned by `GET /onboard/infer`. The flow cannot be computed until inference returns. Before that, show a loading spinner.

**On every page mount, inference runs first** (idempotent, fast). This means:
- Fresh user → spinner → inference returns → `computeFlow(hasHistory, whatsappEnabled)` → render first step
- Resuming user → spinner → inference runs again → flow computed → jump to resume step

This is simpler than caching `hasHistory` in `sessionStorage` and avoids stale-state risk. Re-running inference is safe: it reads Swiggy order history and address, does not write any new data if a household row already exists (the address upsert is idempotent).

**Critical ordering constraint:** `GET /onboard/status` and `GET /onboard/infer` must NOT be called in parallel. Inference must fully resolve first, then resume logic fires. If status resolves before inference completes, the flow array is uncomputed and the resume target (`"phone"`, `"allset"`) cannot be placed into it — causing a race condition. Implementation must be sequential:

```typescript
// correct
const inferRes  = await api.onboard.infer()         // 1. compute flow
const statusRes = await api.onboard.status()         // 2. then determine resume step

// wrong — race condition
const [inferRes, statusRes] = await Promise.all([...]) // do not do this
```

This matters for the Flow B resume case: a user who completed the questionnaire (`profile_saved=true`) but closed before inference ran will re-enter, inference runs (idempotent), flow is computed, then `profile_saved=true` correctly sends them to `"phone"` (or `"allset"`). Without the ordering constraint, they could land in an uncomputed flow.

---

## Resume logic

After inference runs on mount, the resume step is determined from `GET /onboard/status`:

| Status signal | Resume at |
|---|---|
| `onboarding_complete = true` | redirect to `/dashboard` |
| `whatsapp_verified = true` | `"allset"` |
| `profile_saved = true` + WA enabled | `"phone"` |
| `profile_saved = true` + WA disabled | `"allset"` |
| anything else | `flow[0]` (first step of computed flow) |

**Flow A + phone/otp in progress:** `profile_saved` is `false` for Flow A users (they never submit the questionnaire). If a Flow A user has reached `phone` or `otp` and refreshes, they fall through to `flow[0]` which is `"inference"`. They restart from inference — correct and idempotent. No `phone_entered` signal will be added to track partial WA progress; the phone step is fast to redo.

**`profile_saved` definition:** checks `weekly_budget_max is not None` on the backend. Flow A users will never satisfy this. The resume logic above handles this correctly by treating any unmatched state as "start from flow[0]".

---

## Inference step display

**`hasHistory = true`:**
- Show inferred values (diet, address, budget estimate) with light framing: *"Here's what we found from your Swiggy history."*
- No editable form — just confirmation. "Looks right?" framing.

**`hasHistory = false`:**
- Show: *"No previous Swiggy orders found. We'll build your pantry as you shop — the more you order, the smarter we get."*
- Show address if available (even new users have a delivery address)
- No budget / diet shown (not inferred)

---

## Step 7 removal

`Step7BasketPreview` component is removed. Before deleting `handleSkipBasket`, verify it has no other call sites in `onboard/page.tsx` beyond the Step 7 render block.

---

## Files to touch

**Backend:**
- `app/pilot/app/services/onboarding_service.py` — add `has_order_history: bool = False` to `InferenceResult`; set `result.has_order_history = len(orders) > 0` after orders are fetched
- `app/pilot/app/api/onboard.py` — add `"has_order_history": result.has_order_history` to the infer response dict

**Frontend:**
- `app/cockpit/src/app/onboard/page.tsx` — full flow refactor: `computeFlow`, `goNext`/`goBack`, inference-first mount, resume logic, remove Step7

---

## Acceptance criteria

- [ ] `GET /onboard/infer` returns `has_order_history: bool`
- [ ] User with history: inference → (phone + otp if WA enabled) → allset — no questionnaire
- [ ] User without history: household → diet → budget → inference → (phone + otp if WA enabled) → allset
- [ ] `hasHistory` defaults to `false` when infer call fails or field is missing
- [ ] Step 7 (basket preview) is gone from both flows
- [ ] Step bar shows only during questionnaire steps; denominator is `questionnaireSteps.length` (not hardcoded 3)
- [ ] Back button hidden when on `flow[0]`
- [ ] `goBack()` from inference in Flow B returns to budget step
- [ ] Inference is re-run on every page mount; resume logic fires after it returns
- [ ] `whatsapp_verified=true` on resume → `allset`; `profile_saved=true` → `phone` or `allset`
- [ ] Flow A users who refresh mid-phone/otp restart from inference (no crash, no stuck state)
- [ ] Adding a new step requires: new StepKey, new component, one line in `computeFlow`
- [ ] Both flows land on `/dashboard` after All Set

---

## Out of scope

- Collecting preferred order day / delivery slot during onboarding (deferred)
- Editing the inferred basket during onboarding (separate task)
- Animations between steps
