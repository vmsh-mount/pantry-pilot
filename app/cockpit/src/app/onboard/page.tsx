"use client"

/**
 * Onboarding wizard — 8 steps matching the design spec:
 *  1 — Household type (OptionGrid 2×2)
 *  2 — Diet type (OptionList)
 *  3 — Budget preset (BudgetGrid 2×2)
 *  4 — Inference summary (pre-fill review)
 *  5 — WhatsApp number entry
 *  6 — WhatsApp OTP (6-box OtpInput)
 *  7 — Basket preview (grouped by category, BudgetBar, SubstitutionBanner)
 *  8 — All set (schedule card + Open WhatsApp CTA)
 */

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { api } from "@/lib/api"
import {
  Shell, Card, StepBar, Button, Input, Alert,
  OptionGrid, OptionList, BudgetGrid, BudgetBar,
  SubstitutionBanner, OtpInput, Spinner,
} from "@/components/ui"

// ── Category emoji map ────────────────────────────────────────────────────────

const CATEGORY_EMOJI: Record<string, string> = {
  staples:    "🌾",
  dairy:      "🥛",
  vegetables: "🥬",
  fruits:     "🍎",
  spices:     "🌶️",
  bakery:     "🍞",
  beverages:  "🧃",
  snacks:     "🍪",
  cleaning:   "🧹",
  personal:   "🧴",
  grocery:    "🛒",
}

function inferCategory(name: string): string {
  const n = name.toLowerCase()
  if (/\b(rice|atta|flour|dal|lentil|pulse|poha|suji|semolina|maida|bread|roti|chilla)\b/.test(n)) return "staples"
  if (/\b(milk|curd|yogurt|paneer|cheese|butter|ghee|cream)\b/.test(n)) return "dairy"
  if (/\b(tomato|onion|potato|carrot|spinach|cabbage|cauliflower|brinjal|peas|beans|gourd|capsicum|cucumber|ladies finger|okra)\b/.test(n)) return "vegetables"
  if (/\b(apple|banana|mango|grape|orange|lemon|pomegranate|watermelon|papaya|guava)\b/.test(n)) return "fruits"
  if (/\b(salt|sugar|oil|mustard|cumin|turmeric|chilli|pepper|masala|spice|haldi|jeera)\b/.test(n)) return "spices"
  if (/\b(biscuit|cookie|snack|chips|popcorn|namkeen|wafer|chocolate)\b/.test(n)) return "snacks"
  if (/\b(tea|coffee|juice|water|soda|drink|cocoa|horlicks|boost|bournvita)\b/.test(n)) return "beverages"
  if (/\b(detergent|soap|shampoo|toothpaste|cleaner|vim|surf|dettol|sanitizer|dishwash)\b/.test(n)) return "cleaning"
  if (/\b(lotion|cream|conditioner|hair oil|face wash|body wash|perfume|deodorant)\b/.test(n)) return "personal"
  return "grocery"
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface BasketItem {
  item_name:           string
  product_name?:       string
  brand?:              string
  quantity:            number
  unit:                string
  total_price:         number
  is_substitution?:    boolean
  original_item_name?: string
  add_reason?:         string
  category?:           string
}

interface BasketPreview {
  items:           BasketItem[]
  estimated_total: number
  item_count:      number
  notes:           string[]
}

// ── Budget presets ─────────────────────────────────────────────────────────────

const BUDGET_PRESETS = [
  { min: 500,  max: 1000, label: "₹500 – ₹1,000",  sub: "Solo, light week" },
  { min: 1000, max: 1500, label: "₹1,000 – ₹1,500", sub: "Couple, moderate" },
  { min: 1500, max: 2500, label: "₹1,500 – ₹2,500", sub: "Family, balanced" },
  { min: 2500, max: 4000, label: "₹2,500 – ₹4,000", sub: "Large household" },
]

// ── Step 1: Household type ────────────────────────────────────────────────────

function Step1Household({
  value,
  onChange,
  onNext,
}: {
  value: string
  onChange: (v: string) => void
  onNext: () => void
}) {
  return (
    <div className="px-6 pb-6 space-y-5">
      <OptionGrid
        value={value as "solo" | "couple" | "family" | "joint_family"}
        onChange={(v) => { onChange(v); }}
        options={[
          { value: "solo",         emoji: "🧑", label: "Just me",     sub: "1 person" },
          { value: "couple",       emoji: "👫", label: "Couple",       sub: "2 people" },
          { value: "family",       emoji: "👨‍👩‍👧", label: "Family",       sub: "3–5 people" },
          { value: "joint_family", emoji: "🏠", label: "Joint family", sub: "6+ people" },
        ]}
      />
      <Button onClick={onNext} disabled={!value}>Continue →</Button>
    </div>
  )
}

// ── Step 2: Diet type ─────────────────────────────────────────────────────────

function Step2Diet({
  value,
  onChange,
  onNext,
  onBack,
}: {
  value: string
  onChange: (v: string) => void
  onNext: () => void
  onBack: () => void
}) {
  return (
    <div className="px-6 pb-6 space-y-5">
      <OptionList
        value={value as "vegetarian" | "vegan" | "jain" | "non_vegetarian"}
        onChange={(v) => { onChange(v) }}
        options={[
          { value: "vegetarian",     emoji: "🥗", label: "Vegetarian",    sub: "No meat or eggs" },
          { value: "vegan",          emoji: "🌱", label: "Vegan",          sub: "No animal products" },
          { value: "jain",           emoji: "🕊️", label: "Jain",           sub: "No root vegetables" },
          { value: "non_vegetarian", emoji: "🍗", label: "Non-vegetarian", sub: "Includes meat & eggs" },
        ]}
      />
      <Button onClick={onNext} disabled={!value}>Continue →</Button>
      <Button variant="ghost" onClick={onBack}>← Back</Button>
    </div>
  )
}

// ── Step 3: Budget ────────────────────────────────────────────────────────────

function Step3Budget({
  budgetMax,
  onSelect,
  onNext,
  onBack,
}: {
  budgetMax: number
  onSelect: (min: number, max: number) => void
  onNext: () => void
  onBack: () => void
}) {
  return (
    <div className="px-6 pb-6 space-y-5">
      <BudgetGrid
        presets={BUDGET_PRESETS}
        value={budgetMax}
        onChange={onSelect}
      />
      <Button onClick={onNext} disabled={!budgetMax}>Continue →</Button>
      <Button variant="ghost" onClick={onBack}>← Back</Button>
    </div>
  )
}

// ── Step 4: Inference summary ─────────────────────────────────────────────────

function Step4Inference({
  infer,
  loading,
  onNext,
  onBack,
}: {
  infer: Record<string, unknown> | null
  loading: boolean
  onNext: () => void
  onBack: () => void
}) {
  if (loading) {
    return (
      <div className="flex flex-col items-center py-12 gap-3 text-gray-400">
        <Spinner size="lg" />
        <p className="text-sm">Analysing your Swiggy history…</p>
      </div>
    )
  }

  return (
    <div className="px-6 pb-6 space-y-4">
      <div className="bg-[#F7F8F5] rounded-2xl p-4 space-y-3">
        {infer ? (
          <>
            {infer.address_line && (
              <SummaryRow icon="📍" label="Delivery address" value={infer.address_line as string} />
            )}
            {infer.diet_type && (
              <SummaryRow icon="🥗" label="Diet detected" value={String(infer.diet_type).replace("_", " ")} />
            )}
            {infer.weekly_budget_max && (
              <SummaryRow icon="💰" label="Budget estimate" value={`₹${infer.weekly_budget_max}/week`} />
            )}
            {infer.confidence_notes && Array.isArray(infer.confidence_notes) && infer.confidence_notes.length > 0 && (
              <SummaryRow icon="✨" label="Notes" value={(infer.confidence_notes as string[]).join(", ")} />
            )}
          </>
        ) : (
          <p className="text-sm text-gray-500 text-center py-2">
            No previous orders found — we'll personalise as you go!
          </p>
        )}
      </div>
      <p className="text-xs text-gray-400 text-center">
        These are pre-filled from your Swiggy history. You can update them in Settings any time.
      </p>
      <Button onClick={onNext}>Looks good →</Button>
      <Button variant="ghost" onClick={onBack}>← Back</Button>
    </div>
  )
}

function SummaryRow({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <div className="flex items-start gap-3">
      <span className="text-lg mt-0.5">{icon}</span>
      <div>
        <p className="text-xs text-gray-500">{label}</p>
        <p className="text-sm font-semibold text-gray-800 capitalize">{value}</p>
      </div>
    </div>
  )
}

// ── Step 5: WhatsApp number ───────────────────────────────────────────────────

function Step5Phone({
  onSent,
  onBack,
}: {
  onSent: (phone: string) => void
  onBack: () => void
}) {
  const [phone,   setPhone]   = useState("")
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState("")

  async function handleSend(e: React.FormEvent) {
    e.preventDefault()
    const digits = phone.replace(/\D/g, "")
    const local = digits.startsWith("91") && digits.length === 12 ? digits.slice(2) : digits
    if (local.length !== 10) {
      setError("Enter a valid 10-digit Indian WhatsApp number.")
      return
    }
    setLoading(true); setError("")
    const res = await api.onboard.sendOtp({ whatsapp_number: `+91${local}` })
    setLoading(false)
    if (res.success) {
      onSent(`+91${local}`)
    } else {
      setError((res.error as { message?: string })?.message ?? "Failed to send OTP.")
    }
  }

  return (
    <form onSubmit={handleSend} className="px-6 pb-6 space-y-4">
      <div className="bg-[#D8F3DC] rounded-2xl p-4 flex items-start gap-3">
        <span className="text-2xl">💬</span>
        <p className="text-sm text-[#1B4332]">
          We'll send your weekly basket to WhatsApp for you to approve — before anything is ordered.
        </p>
      </div>

      <div className="flex items-center border-2 border-gray-200 focus-within:border-[#2D6A4F] rounded-xl overflow-hidden transition-colors">
        <span className="px-3 py-3 text-sm text-gray-500 bg-[#F7F8F5] border-r border-gray-200 font-medium">
          +91
        </span>
        <input
          type="tel"
          placeholder="98765 43210"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          className="flex-1 px-3 py-3 text-sm outline-none bg-white"
          maxLength={15}
        />
      </div>

      {error && <Alert type="error" message={error} />}

      <Button type="submit" loading={loading}>Send code on WhatsApp →</Button>
      <Button variant="ghost" onClick={onBack} type="button">← Back</Button>
    </form>
  )
}

// ── Step 6: OTP verification ──────────────────────────────────────────────────

function Step6Otp({
  phone,
  onVerified,
  onChangeNumber,
}: {
  phone: string
  onVerified: () => void
  onChangeNumber: () => void
}) {
  const [otp,       setOtp]       = useState("")
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState("")
  const [countdown, setCountdown] = useState(60)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    timerRef.current = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) { clearInterval(timerRef.current!); return 0 }
        return c - 1
      })
    }, 1000)
    return () => clearInterval(timerRef.current!)
  }, [])

  async function handleVerify(e: React.FormEvent) {
    e.preventDefault()
    if (otp.length !== 6) { setError("Enter the 6-digit code."); return }
    setLoading(true); setError("")
    const res = await api.onboard.verifyOtp({ whatsapp_number: phone, otp })
    setLoading(false)
    if (res.success) {
      onVerified()
    } else {
      setError((res.error as { message?: string })?.message ?? "Incorrect code. Try again.")
    }
  }

  async function resend() {
    await api.onboard.sendOtp({ whatsapp_number: phone })
    setCountdown(60)
    timerRef.current = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) { clearInterval(timerRef.current!); return 0 }
        return c - 1
      })
    }, 1000)
  }

  return (
    <form onSubmit={handleVerify} className="px-6 pb-6 space-y-5">
      <div className="text-center">
        <p className="text-sm text-gray-600">
          Code sent to <span className="font-semibold text-gray-900">{phone}</span>
        </p>
        <button
          type="button"
          onClick={onChangeNumber}
          className="text-xs text-[#2D6A4F] hover:underline mt-0.5"
        >
          Change number
        </button>
      </div>

      <OtpInput value={otp} onChange={setOtp} />

      {error && <Alert type="error" message={error} />}

      <Button type="submit" loading={loading} disabled={otp.length < 6}>
        Verify →
      </Button>

      <div className="text-center text-xs text-gray-400">
        {countdown > 0 ? (
          <span>Resend in {countdown}s</span>
        ) : (
          <button
            type="button"
            onClick={resend}
            className="text-[#2D6A4F] font-medium hover:underline"
          >
            Resend code
          </button>
        )}
      </div>
    </form>
  )
}

// ── Step 7: Basket preview ────────────────────────────────────────────────────

function Step7BasketPreview({
  budgetMax,
  onNext,
  onSkip,
}: {
  budgetMax: number
  onNext: () => void
  onSkip: () => void
}) {
  const [preview,  setPreview]  = useState<BasketPreview | null>(null)
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState("")
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    api.onboard.basketPreview().then((res) => {
      setLoading(false)
      if (res.success && res.data) {
        setPreview(res.data as BasketPreview)
      } else {
        setError("Couldn't load your basket. You can skip this step.")
      }
    })
  }, [])

  if (loading) {
    return (
      <div className="flex flex-col items-center py-12 gap-3 text-gray-400">
        <Spinner size="lg" />
        <p className="text-sm">Building your first basket…</p>
        <p className="text-xs text-gray-300">Checking Instamart availability</p>
      </div>
    )
  }

  const items = preview?.items ?? []
  const subs = items.filter((it) => it.is_substitution)

  // Group by category
  const grouped: Record<string, BasketItem[]> = {}
  items.forEach((item) => {
    const cat = item.category || inferCategory(item.item_name)
    if (!grouped[cat]) grouped[cat] = []
    grouped[cat].push(item)
  })

  const SHOW_LIMIT = 5
  const allItems = items
  const visibleItems = expanded ? allItems : allItems.slice(0, SHOW_LIMIT)
  const hiddenCount = allItems.length - SHOW_LIMIT

  return (
    <div className="pb-6 space-y-4">
      {/* Green header */}
      <div className="bg-[#2D6A4F] px-6 py-5 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-white font-bold text-base">Your basket this week 🛒</h3>
          <span className="bg-white/20 text-white text-xs font-semibold px-2.5 py-1 rounded-full">
            {preview?.item_count ?? 0} items
          </span>
        </div>
        {budgetMax > 0 && preview && (
          <BudgetBar
            spent={preview.estimated_total}
            budget={budgetMax}
          />
        )}
      </div>

      <div className="px-6 space-y-3">
        {error ? (
          <Alert type="info" message={error} />
        ) : preview && items.length > 0 ? (
          <>
            {subs.length > 0 && <SubstitutionBanner items={subs} />}

            {/* Items list (flat, with category emoji inline) */}
            <div className="bg-[#F7F8F5] rounded-2xl overflow-hidden divide-y divide-gray-100">
              {visibleItems.map((item, i) => {
                const cat = item.category || inferCategory(item.item_name)
                const emoji = CATEGORY_EMOJI[cat] ?? "🛒"
                const reason = item.add_reason?.includes("llm")
                  ? "✨ Suggested for you"
                  : item.is_substitution
                  ? undefined
                  : "Running low · reorder"
                return (
                  <div key={i} className="flex items-center gap-3 px-4 py-3">
                    <span className="text-xl shrink-0">{emoji}</span>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-800 truncate">
                        {item.product_name || item.item_name}
                      </p>
                      {reason && (
                        <p className="text-xs text-gray-400 mt-0.5">{reason}</p>
                      )}
                    </div>
                    <p className="text-sm font-semibold text-gray-800 shrink-0">
                      ₹{Math.round(item.total_price)}
                    </p>
                  </div>
                )
              })}
            </div>

            {!expanded && hiddenCount > 0 && (
              <button
                type="button"
                onClick={() => setExpanded(true)}
                className="w-full text-sm text-[#2D6A4F] font-medium py-2 hover:underline"
              >
                Show {hiddenCount} more item{hiddenCount > 1 ? "s" : ""}
              </button>
            )}

            {/* Estimated total */}
            <div className="flex items-center justify-between bg-[#D8F3DC] rounded-xl px-4 py-3">
              <span className="text-sm font-medium text-[#1B4332]">Estimated total</span>
              <span className="text-base font-bold text-[#1B4332]">
                ₹{Math.round(preview.estimated_total).toLocaleString("en-IN")}
              </span>
            </div>
          </>
        ) : (
          <div className="text-center py-8">
            <p className="text-4xl mb-3">🛒</p>
            <p className="text-sm text-gray-600 font-medium">No previous orders found</p>
            <p className="text-xs text-gray-400 mt-1">
              PantryPilot will build your pantry as you order
            </p>
          </div>
        )}

        {/* Action stack */}
        <div className="space-y-2 pt-2">
          <Button onClick={onNext}>
            📲 Send to WhatsApp for review
          </Button>
          <Button variant="secondary" onClick={onNext}>
            Set a weekly schedule instead
          </Button>
          <Button variant="ghost" onClick={onSkip}>
            Skip for now
          </Button>
        </div>

        <p className="text-center text-xs text-gray-400">
          Nothing is ordered yet. You'll confirm on WhatsApp first.
        </p>
      </div>
    </div>
  )
}

// ── Step 8: All set ───────────────────────────────────────────────────────────

function Step8AllSet({
  onFinish,
}: {
  onFinish: (placeNow: boolean) => void
}) {
  const [loading, setLoading] = useState(false)

  return (
    <div className="px-6 pb-6 space-y-5">
      <div className="text-center space-y-2">
        <div className="text-5xl mb-3">🎉</div>
        <h3 className="text-xl font-bold text-gray-900">You're all set!</h3>
        <p className="text-sm text-gray-500">
          PantryPilot will plan and send your weekly basket every week.
          Nothing ships without your approval.
        </p>
      </div>

      {/* Schedule card */}
      <div className="bg-[#F7F8F5] border border-[#D8F3DC] rounded-2xl p-4 space-y-3">
        <p className="text-xs font-semibold text-[#2D6A4F] uppercase tracking-wide">How it works</p>
        {[
          { icon: "📅", text: "Basket planned every week on your order day" },
          { icon: "💬", text: "Sent to WhatsApp for your approval" },
          { icon: "✅", text: "You confirm — then it's ordered instantly" },
          { icon: "⏸️",  text: "Pause or cancel any time in Settings" },
        ].map((r) => (
          <div key={r.text} className="flex items-center gap-3 text-sm text-gray-700">
            <span className="text-base">{r.icon}</span>
            {r.text}
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <Button
          loading={loading}
          onClick={() => { setLoading(true); onFinish(true) }}
        >
          🛒 Place my first order now
        </Button>
        <Button variant="secondary" onClick={() => onFinish(false)}>
          Schedule for my usual day
        </Button>
      </div>

      <p className="text-center text-xs text-gray-400">
        You can update your schedule any time in Settings
      </p>
    </div>
  )
}

// ── Root onboarding page ──────────────────────────────────────────────────────

const QUESTIONNAIRE_STEPS = 3  // steps 1–3 show the step bar

const STEP_META: { icon: string; title: string; subtitle: string }[] = [
  { icon: "🏠", title: "Who's in your household?",     subtitle: "We'll plan the right quantities for you." },
  { icon: "🥗", title: "What's your diet preference?", subtitle: "We'll filter out items that don't match." },
  { icon: "💰", title: "What's your weekly budget?",   subtitle: "We'll keep your basket within range." },
  { icon: "✨", title: "Here's what we found",          subtitle: "Based on your Swiggy order history." },
  { icon: "📱", title: "Connect WhatsApp",              subtitle: "We'll send your basket here every week." },
  { icon: "🔒", title: "Enter verification code",       subtitle: "Check your WhatsApp messages." },
  { icon: "🛒", title: "Your first basket preview",    subtitle: "" },
  { icon: "🚀", title: "You're ready to go!",           subtitle: "" },
]

export default function OnboardPage() {
  const router = useRouter()
  const [step, setStep] = useState(1)
  const [resumeChecked,    setResumeChecked]    = useState(false)
  const [whatsappEnabled,  setWhatsappEnabled]  = useState(true)

  // Form state
  const [householdType, setHouseholdType] = useState("couple")
  const [dietType,      setDietType]      = useState("vegetarian")
  const [budgetMin,     setBudgetMin]     = useState(1500)
  const [budgetMax,     setBudgetMax]     = useState(2500)
  const [phone,         setPhone]         = useState("")
  const [infer,         setInfer]         = useState<Record<string, unknown> | null>(null)
  const [inferLoading,  setInferLoading]  = useState(false)
  const [error,         setError]         = useState("")

  // On mount: check backend status and resume at the correct step
  useEffect(() => {
    // Fetch whatsapp_enabled flag — default true on failure so steps are never
    // accidentally skipped due to a transient settings error
    api.settings.get().then((res) => {
      if (res.success && res.data) {
        setWhatsappEnabled((res.data as import("@/lib/api").SettingsResponse).whatsapp_enabled ?? true)
      }
    }).catch(() => {})

    api.onboard.status().then((res) => {
      if (!res.success) {
        // Session points to a deleted household (e.g. after make nuke) — go to login
        router.replace("/")
        return
      }
      if (res.success && res.data) {
        const d = res.data as Record<string, unknown>
        // If onboarding already fully complete, redirect to dashboard
        if (d.onboarding_complete) {
          router.replace("/basket")
          return
        }
        // Resume at the right step
        if (d.whatsapp_verified) {
          // Profile + WhatsApp already done — jump to basket preview
          if (d.diet_type)           setDietType(d.diet_type as string)
          if (d.weekly_budget_min)   setBudgetMin(d.weekly_budget_min as number)
          if (d.weekly_budget_max)   setBudgetMax(d.weekly_budget_max as number)
          if (d.household_type)      setHouseholdType(d.household_type as string)
          setStep(7)
        } else if (d.profile_saved) {
          // Profile saved but WhatsApp not yet verified
          if (d.diet_type)           setDietType(d.diet_type as string)
          if (d.weekly_budget_min)   setBudgetMin(d.weekly_budget_min as number)
          if (d.weekly_budget_max)   setBudgetMax(d.weekly_budget_max as number)
          if (d.household_type)      setHouseholdType(d.household_type as string)
          if (d.whatsapp_number)     setPhone(d.whatsapp_number as string)
          // Skip phone/OTP steps when WhatsApp is disabled
          setStep(whatsappEnabled ? 5 : 7)
        }
        // else: fresh start, keep step=1
      }
      setResumeChecked(true)
    }).catch(() => setResumeChecked(true))
  }, [])

  // Run inference when we reach step 4
  useEffect(() => {
    if (step === 4 && infer === null && !inferLoading) {
      setInferLoading(true)
      api.onboard.infer().then((res) => {
        setInferLoading(false)
        if (res.success && res.data) {
          const d = res.data as Record<string, unknown>
          setInfer(d)
          // Pre-fill diet + budget from inference
          if (d.diet_type)         setDietType(d.diet_type as string)
          if (d.weekly_budget_min) setBudgetMin(d.weekly_budget_min as number)
          if (d.weekly_budget_max) setBudgetMax(d.weekly_budget_max as number)
        } else {
          setInfer({})
        }
      })
    }
  }, [step])

  // Save profile when advancing past step 3
  async function saveProfileAndAdvance() {
    const res = await api.onboard.saveProfile({
      household_type:    householdType as "solo" | "couple" | "family" | "joint_family",
      member_count:      householdType === "solo" ? 1 : householdType === "couple" ? 2 : householdType === "family" ? 4 : 8,
      diet_type:         dietType as "vegetarian" | "vegan" | "jain" | "non_vegetarian",
      weekly_budget_min: budgetMin,
      weekly_budget_max: budgetMax,
      allergies:         [],
    })
    if (res.success) {
      setStep(4)
    } else {
      setError((res.error as { message?: string })?.message ?? "Could not save profile.")
    }
  }

  async function handleComplete(placeNow: boolean) {
    setError("")
    const res = await api.onboard.complete({ place_order_now: placeNow })
    if (res.success) {
      router.push(placeNow ? "/onboard/placing" : "/onboard/done")
    } else {
      setError((res.error as { message?: string })?.message ?? "Something went wrong.")
    }
  }

  async function handleSkipBasket() {
    router.push("/onboard/done")
  }

  const meta = STEP_META[step - 1]
  const showStepBar = step <= QUESTIONNAIRE_STEPS

  // Show a blank shell while we check resume state to avoid step-1 flash
  if (!resumeChecked) {
    return (
      <Shell>
        <div className="flex items-center justify-between px-1 mb-4">
          <div className="flex items-center gap-2 text-white">
            <span className="text-xl">🥦</span>
            <span className="font-bold">PantryPilot</span>
          </div>
        </div>
        <Card>
          <div className="flex flex-col items-center py-16 gap-3 text-gray-400">
            <Spinner size="lg" />
          </div>
        </Card>
      </Shell>
    )
  }

  return (
    <Shell>
      {/* Logo bar */}
      <div className="flex items-center justify-between px-1 mb-4">
        <div className="flex items-center gap-2 text-white">
          <span className="text-xl">🥦</span>
          <span className="font-bold">PantryPilot</span>
        </div>
        <span className="text-[#D8F3DC] text-xs">
          {step < 7 ? `Step ${step} of 8` : ""}
        </span>
      </div>

      <Card>
        {/* Progress bar — only for questionnaire steps */}
        {showStepBar && (
          <StepBar step={step} total={QUESTIONNAIRE_STEPS} />
        )}

        {/* Section header — skip for basket preview (step 7) which has its own header */}
        {step !== 7 && (
          <div className="px-6 pt-5 pb-2">
            <div className="text-3xl mb-2">{meta.icon}</div>
            <h2 className="text-xl font-bold text-gray-900">{meta.title}</h2>
            {meta.subtitle && (
              <p className="text-sm text-gray-500 mt-1">{meta.subtitle}</p>
            )}
          </div>
        )}

        {error && (
          <div className="px-6 pb-2">
            <Alert type="error" message={error} />
          </div>
        )}

        {step === 1 && (
          <Step1Household
            value={householdType}
            onChange={setHouseholdType}
            onNext={() => setStep(2)}
          />
        )}

        {step === 2 && (
          <Step2Diet
            value={dietType}
            onChange={setDietType}
            onNext={() => setStep(3)}
            onBack={() => setStep(1)}
          />
        )}

        {step === 3 && (
          <Step3Budget
            budgetMax={budgetMax}
            onSelect={(min, max) => { setBudgetMin(min); setBudgetMax(max) }}
            onNext={saveProfileAndAdvance}
            onBack={() => setStep(2)}
          />
        )}

        {step === 4 && (
          <Step4Inference
            infer={infer}
            loading={inferLoading}
            onNext={() => setStep(whatsappEnabled ? 5 : 7)}
            onBack={() => setStep(3)}
          />
        )}

        {step === 5 && (
          <Step5Phone
            onSent={(p) => { setPhone(p); setStep(6) }}
            onBack={() => setStep(4)}
          />
        )}

        {step === 6 && (
          <Step6Otp
            phone={phone}
            onVerified={() => setStep(7)}
            onChangeNumber={() => setStep(5)}
          />
        )}

        {step === 7 && (
          <Step7BasketPreview
            budgetMax={budgetMax}
            onNext={() => setStep(8)}
            onSkip={handleSkipBasket}
          />
        )}

        {step === 8 && (
          <Step8AllSet onFinish={handleComplete} />
        )}
      </Card>
    </Shell>
  )
}
