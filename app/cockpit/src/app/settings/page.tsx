"use client"

/**
 * Settings page — household preferences + account management.
 *
 * Sections:
 *  - Delivery & schedule   (order day, delivery slot, confirmation window)
 *  - Diet & household      (diet type, member count, allergies, budget)
 *  - Automation            (pause / resume toggle)
 *  - Account               (logout, delete account)
 *
 * All edits auto-save on blur / change (PATCH /settings).
 * Pause/resume and delete are deliberate actions with confirmation.
 */

import { useEffect, useState, useCallback, useRef } from "react"
import { useRouter } from "next/navigation"
import { api } from "@/lib/api"
import {
  AppShell, Card, Button, Alert, Spinner,
  SegmentedSelect, Input, Toggle,
} from "@/components/ui"

// ── Types ─────────────────────────────────────────────────────────────────────

interface HouseholdSettings {
  household_id:      string
  household_type:    string
  member_count:      number
  diet_type:         string
  allergies:         string[]
  weekly_budget_min: number
  weekly_budget_max: number
  whatsapp_number:   string
  whatsapp_verified:  boolean
  onboarding_complete: boolean
  is_paused:          boolean
  is_active:         boolean
  preferences: {
    preferred_order_day:     string
    preferred_delivery_slot: string
    confirmation_window_hrs: number
    next_run_at:             string | null
    last_run_at:             string | null
    freq_staples:            string
    freq_fresh_produce:      string
    freq_dairy_eggs:         string
    freq_packaged:           string
  }
}

// ── Section wrapper ───────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="px-6 py-5 border-b border-gray-50 last:border-0 space-y-4">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">{title}</h3>
      {children}
    </div>
  )
}

// ── Save indicator ────────────────────────────────────────────────────────────

function SaveIndicator({ saving }: { saving: boolean }) {
  if (!saving) return null
  return (
    <span className="flex items-center gap-1 text-xs text-green-600">
      <Spinner size="sm" /> Saving…
    </span>
  )
}

// ── Confirm dialog ────────────────────────────────────────────────────────────

function ConfirmDialog({
  title,
  body,
  confirmLabel,
  confirmClass,
  onConfirm,
  onCancel,
}: {
  title:        string
  body:         string
  confirmLabel: string
  confirmClass: string
  onConfirm:    () => void
  onCancel:     () => void
}) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-end justify-center p-4 z-50">
      <div className="bg-white rounded-3xl w-full max-w-sm p-6 space-y-4 shadow-2xl">
        <h3 className="font-bold text-gray-900">{title}</h3>
        <p className="text-sm text-gray-500">{body}</p>
        <div className="flex gap-3">
          <button
            onClick={onCancel}
            className="flex-1 py-3 rounded-2xl bg-gray-100 text-gray-700 font-semibold text-sm"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`flex-1 py-3 rounded-2xl font-semibold text-sm ${confirmClass}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main settings page ────────────────────────────────────────────────────────

export default function SettingsPage() {
  const router = useRouter()

  const [settings, setSettings] = useState<HouseholdSettings | null>(null)
  const [loading,  setLoading]  = useState(true)
  const [saving,   setSaving]   = useState(false)
  const [error,    setError]    = useState("")
  const [dialog,   setDialog]   = useState<"pause" | "resume" | "delete" | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [lifecycleOpen,    setLifecycleOpen]    = useState(false)
  const [lifecycleSuccess, setLifecycleSuccess] = useState("")
  const [lifecycleLoading, setLifecycleLoading] = useState<string | null>(null)
  const [dietSubOpen,      setDietSubOpen]      = useState(false)
  const [pendingDietEvent, setPendingDietEvent] = useState<string | null>(null)
  const successTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Load settings on mount
  useEffect(() => {
    api.settings.get().then((res) => {
      setLoading(false)
      if (res.success && res.data) {
        const raw = res.data as unknown as HouseholdSettings
        // Redirect to onboarding if not yet complete
        if (!raw.onboarding_complete) {
          router.push("/onboard")
          return
        }
        // Normalize nulls → safe defaults so <input value> is never null
        setSettings({
          ...raw,
          whatsapp_number:   raw.whatsapp_number   ?? "",
          household_type:    raw.household_type     ?? "family",
          member_count:      raw.member_count       ?? 2,
          diet_type:         raw.diet_type          ?? "vegetarian",
          allergies:         raw.allergies          ?? [],
          weekly_budget_min: raw.weekly_budget_min  ?? 1000,
          weekly_budget_max: raw.weekly_budget_max  ?? 3000,
          preferences: {
            ...raw.preferences,
            preferred_order_day:     raw.preferences?.preferred_order_day     ?? "sunday",
            preferred_delivery_slot: raw.preferences?.preferred_delivery_slot ?? "morning",
            confirmation_window_hrs: raw.preferences?.confirmation_window_hrs ?? 6,
            freq_staples:            raw.preferences?.freq_staples            ?? "weekly",
            freq_fresh_produce:      raw.preferences?.freq_fresh_produce      ?? "weekly",
            freq_dairy_eggs:         raw.preferences?.freq_dairy_eggs         ?? "weekly",
            freq_packaged:           raw.preferences?.freq_packaged           ?? "fortnightly",
            next_run_at:             raw.preferences?.next_run_at             ?? null,
            last_run_at:             raw.preferences?.last_run_at             ?? null,
          },
        })
      } else if (res.error?.code === "NOT_AUTHENTICATED" || res.error?.code === "TOKEN_EXPIRED") {
        router.push("/")
      } else {
        router.push("/")
      }
    })
  }, [])

  // Debounced auto-save
  const save = useCallback(async (patch: Record<string, unknown>) => {
    setSaving(true)
    setError("")
    const res = await api.settings.update(patch)
    if (!res.success) {
      setError(res.error?.message ?? "Failed to save changes.")
    }
    setSaving(false)
  }, [])

  function update<K extends keyof HouseholdSettings>(key: K, value: HouseholdSettings[K]) {
    if (!settings) return
    const next = { ...settings, [key]: value }
    setSettings(next)
    save({ [key]: value })
  }

  function updatePref(key: string, value: unknown) {
    if (!settings) return
    const next = { ...settings, preferences: { ...settings.preferences, [key]: value } }
    setSettings(next)
    save({ [key]: value })
  }

  async function sendLifecycleSignal(eventType: string) {
    setLifecycleLoading(eventType)
    try {
      await fetch("/api/v1/settings/lifecycle-signal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_type: eventType }),
        credentials: "include",
      })
    } catch { /* non-critical */ }
    setLifecycleLoading(null)
    setLifecycleSuccess("Got it — your next basket will reflect this.")
    setLifecycleOpen(false)
    setDietSubOpen(false)
    if (successTimerRef.current) clearTimeout(successTimerRef.current)
    successTimerRef.current = setTimeout(() => setLifecycleSuccess(""), 4000)
  }

  async function handleDietLifecycle(newDiet: string) {
    // First PATCH the diet type in settings
    update("diet_type", newDiet as HouseholdSettings["diet_type"])
    // Then send lifecycle signal
    if (pendingDietEvent) await sendLifecycleSignal(pendingDietEvent)
    setDietSubOpen(false)
    setPendingDietEvent(null)
  }

  async function handlePause() {
    setActionLoading(true)
    await api.settings.pause("user_request")
    setSettings((s) => s ? { ...s, is_paused: true } : s)
    setDialog(null)
    setActionLoading(false)
  }

  async function handleResume() {
    setActionLoading(true)
    await api.settings.resume()
    setSettings((s) => s ? { ...s, is_paused: false } : s)
    setDialog(null)
    setActionLoading(false)
  }

  async function handleDelete() {
    setActionLoading(true)
    await api.settings.delete()
    router.push("/?deleted=1")
  }

  // ── Render states ────────────────────────────────────────────────────────

  if (loading) {
    return (
      <AppShell>
        <div className="flex justify-center py-20 text-white">
          <Spinner size="lg" />
        </div>
      </AppShell>
    )
  }

  if (!settings) {
    return (
      <AppShell>
        <Card className="p-6">
          <Alert type="error" message={error || "Could not load settings."} />
          <div className="mt-4">
            <Button onClick={() => router.push("/")}>Go home</Button>
          </div>
        </Card>
      </AppShell>
    )
  }

  const prefs = settings.preferences

  return (
    <AppShell>
      <div className="flex items-center justify-between px-1 mb-4">
        <h1 className="text-white font-bold text-lg">Settings</h1>
        <SaveIndicator saving={saving} />
      </div>

      <Card>
        {/* Header */}
        <div className="bg-[#2D6A4F] px-6 py-6 text-white">
          <h1 className="text-xl font-bold">Settings</h1>
          <p className="text-green-300 text-sm mt-1">{settings.whatsapp_number}</p>
          {prefs.next_run_at && (
            <p className="text-green-200 text-xs mt-1">
              Next basket:{" "}
              {new Date(prefs.next_run_at).toLocaleDateString("en-IN", {
                weekday: "short", day: "numeric", month: "short"
              })}
            </p>
          )}
        </div>

        {error && (
          <div className="px-6 pt-4">
            <Alert type="error" message={error} />
          </div>
        )}

        {/* Automation toggle */}
        <Section title="Automation">
          <Toggle
            checked={!settings.is_paused}
            onChange={(active) => setDialog(active ? "resume" : "pause")}
            label="Weekly grocery planning"
            description={
              settings.is_paused
                ? "Paused — tap to resume"
                : "Active — basket sent every week"
            }
          />
        </Section>

        {/* Delivery & schedule */}
        <Section title="Delivery & schedule">
          <SegmentedSelect
            label="Preferred order day"
            value={prefs.preferred_order_day as "sunday"}
            onChange={(v) => updatePref("preferred_order_day", v)}
            options={[
              { value: "monday",    label: "Mon" },
              { value: "tuesday",   label: "Tue" },
              { value: "wednesday", label: "Wed" },
              { value: "thursday",  label: "Thu" },
              { value: "friday",    label: "Fri" },
              { value: "saturday",  label: "Sat" },
              { value: "sunday",    label: "Sun" },
            ]}
          />

          <SegmentedSelect
            label="Preferred delivery slot"
            value={prefs.preferred_delivery_slot as "morning"}
            onChange={(v) => updatePref("preferred_delivery_slot", v)}
            options={[
              { value: "morning",   label: "Morning",   icon: "🌅" },
              { value: "afternoon", label: "Afternoon", icon: "☀️" },
              { value: "evening",   label: "Evening",   icon: "🌆" },
            ]}
          />

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Confirmation window
            </label>
            <div className="flex gap-2">
              {[2, 4, 6].map((hrs) => (
                <button
                  key={hrs}
                  onClick={() => updatePref("confirmation_window_hrs", hrs)}
                  className={`flex-1 py-2.5 rounded-xl text-sm font-medium border transition-all
                    ${prefs.confirmation_window_hrs === hrs
                      ? "bg-green-700 text-white border-green-700"
                      : "bg-white text-gray-700 border-gray-200 hover:border-green-400"
                    }`}
                >
                  {hrs}h
                </button>
              ))}
            </div>
            <p className="text-xs text-gray-400 mt-1.5">
              How long you have to approve the basket before it auto-skips
            </p>
          </div>
        </Section>

        {/* Diet & household */}
        <Section title="Diet & household">
          <SegmentedSelect
            label="Diet preference"
            value={settings.diet_type as "vegetarian"}
            onChange={(v) => update("diet_type", v)}
            options={[
              { value: "vegetarian",     label: "Vegetarian",  icon: "🥗" },
              { value: "vegan",          label: "Vegan",       icon: "🌱" },
              { value: "jain",           label: "Jain",        icon: "🕊️" },
              { value: "non_vegetarian", label: "Non-veg",     icon: "🍗" },
            ]}
          />

          <Input
            label="Household size"
            type="number"
            min={1}
            max={20}
            value={settings.member_count}
            onChange={(e) => update("member_count", parseInt(e.target.value) || 1)}
          />

          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Min budget / week (₹)"
              type="number"
              min={500}
              step={100}
              value={settings.weekly_budget_min}
              onChange={(e) => update("weekly_budget_min", parseInt(e.target.value) || 1000)}
            />
            <Input
              label="Max budget / week (₹)"
              type="number"
              min={500}
              step={100}
              value={settings.weekly_budget_max}
              onChange={(e) => update("weekly_budget_max", parseInt(e.target.value) || 2000)}
            />
          </div>

          <Input
            label="Allergies"
            placeholder="peanut, shellfish, gluten…"
            value={(settings.allergies ?? []).join(", ")}
            onChange={(e) =>
              update("allergies", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))
            }
            hint="Separate with commas"
          />
        </Section>

        {/* Something changed */}
        <Section title="Something changed?">
          {lifecycleSuccess ? (
            <div className="bg-[#D8F3DC] border border-[#2D6A4F]/30 text-[#1B4332] text-sm rounded-2xl px-4 py-3">
              {lifecycleSuccess}
            </div>
          ) : !lifecycleOpen ? (
            <button
              onClick={() => setLifecycleOpen(true)}
              className="w-full flex items-center justify-between px-4 py-3 rounded-2xl border-2 border-dashed border-gray-200 text-sm font-medium text-gray-600 hover:border-[#2D6A4F] hover:text-[#2D6A4F] transition-colors"
            >
              <span>Tell us what shifted</span>
              <span className="text-gray-400">→</span>
            </button>
          ) : (
            <div className="space-y-2">
              {[
                { emoji: "🥗", label: "Diet or eating habits",  eventType: "diet_change",    isDiet: true  },
                { emoji: "👤", label: "Household size changed",  eventType: "new_member",     isDiet: false },
                { emoji: "✈️", label: "Back from a trip",        eventType: "vacation_return", isDiet: false },
                { emoji: "📦", label: "Did a big stock-up",      eventType: "major_restock",  isDiet: false },
              ].map((opt) => (
                <button
                  key={opt.eventType}
                  disabled={!!lifecycleLoading}
                  onClick={() => {
                    if (opt.isDiet) {
                      setPendingDietEvent(opt.eventType)
                      setDietSubOpen(true)
                    } else {
                      sendLifecycleSignal(opt.eventType)
                    }
                  }}
                  className="w-full flex items-center gap-3 px-4 py-3 rounded-2xl bg-[#F7F8F5] hover:bg-[#D8F3DC] transition-colors text-left disabled:opacity-50"
                >
                  <span className="text-xl">{opt.emoji}</span>
                  <span className="text-sm font-medium text-gray-700">{opt.label}</span>
                  {lifecycleLoading === opt.eventType && (
                    <Spinner size="sm" />
                  )}
                </button>
              ))}
              <button
                onClick={() => { setLifecycleOpen(false); setDietSubOpen(false) }}
                className="w-full text-center text-xs text-gray-400 py-1 hover:text-gray-600"
              >
                Never mind
              </button>
            </div>
          )}

          {/* Diet sub-selection (shown inline below options when diet is picked) */}
          {dietSubOpen && (
            <div className="mt-3 space-y-2">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">New diet preference</p>
              {[
                { value: "vegetarian",     emoji: "🥗", label: "Vegetarian"    },
                { value: "vegan",          emoji: "🌱", label: "Vegan"          },
                { value: "jain",           emoji: "🕊️", label: "Jain"           },
                { value: "non_vegetarian", emoji: "🍗", label: "Non-vegetarian" },
              ].map((d) => (
                <button
                  key={d.value}
                  onClick={() => handleDietLifecycle(d.value)}
                  className="w-full flex items-center gap-3 px-4 py-3 rounded-2xl border border-gray-200 bg-white hover:border-[#2D6A4F] transition-colors text-left"
                >
                  <span className="text-xl">{d.emoji}</span>
                  <span className="text-sm font-medium text-gray-700">{d.label}</span>
                </button>
              ))}
              <button
                onClick={() => { setDietSubOpen(false); setPendingDietEvent(null) }}
                className="w-full text-center text-xs text-gray-400 py-1 hover:text-gray-600"
              >
                Cancel
              </button>
            </div>
          )}
        </Section>

        {/* Account */}
        <Section title="Account">
          <div className="space-y-3">
            <Button
              variant="secondary"
              onClick={async () => {
                await api.auth.logout()
                router.push("/")
              }}
            >
              Sign out
            </Button>

            <Button
              variant="danger"
              onClick={() => setDialog("delete")}
            >
              Delete my account
            </Button>
          </div>
          <p className="text-xs text-gray-400 text-center">
            Deleting your account removes all data permanently.
          </p>
        </Section>
      </Card>

      {/* Dialogs */}
      {dialog === "pause" && (
        <ConfirmDialog
          title="Pause grocery planning?"
          body="We'll stop sending weekly baskets until you resume. No orders will be placed."
          confirmLabel={actionLoading ? "Pausing…" : "Yes, pause"}
          confirmClass="bg-gray-800 text-white"
          onConfirm={handlePause}
          onCancel={() => setDialog(null)}
        />
      )}

      {dialog === "resume" && (
        <ConfirmDialog
          title="Resume grocery planning?"
          body="We'll send your next basket on your usual order day."
          confirmLabel={actionLoading ? "Resuming…" : "Yes, resume"}
          confirmClass="bg-green-700 text-white"
          onConfirm={handleResume}
          onCancel={() => setDialog(null)}
        />
      )}

      {dialog === "delete" && (
        <ConfirmDialog
          title="Delete your account?"
          body="This permanently deletes all your data — pantry history, preferences, and order records. This cannot be undone."
          confirmLabel={actionLoading ? "Deleting…" : "Yes, delete everything"}
          confirmClass="bg-red-600 text-white"
          onConfirm={handleDelete}
          onCancel={() => setDialog(null)}
        />
      )}
    </AppShell>
  )
}
