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
    <>
      <div className="flex-1 overflow-y-auto px-6 pt-2 pb-2">
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
      </div>
      <div className="px-6 pb-6 pt-3 space-y-3 border-t border-gray-100">
        <Button onClick={onNext} disabled={!value}>Continue →</Button>
      </div>
    </>
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
  onBack?: () => void
}) {
  return (
    <>
      <div className="flex-1 overflow-y-auto px-6 pt-2 pb-2">
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
      </div>
      <div className="px-6 pb-6 pt-3 space-y-3 border-t border-gray-100">
        <Button onClick={onNext} disabled={!value}>Continue →</Button>
        {onBack && <Button variant="ghost" onClick={onBack}>← Back</Button>}
      </div>
    </>
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
  onBack?: () => void
}) {
  return (
    <>
      <div className="flex-1 overflow-y-auto px-6 pt-2 pb-2">
        <BudgetGrid
          presets={BUDGET_PRESETS}
          value={budgetMax}
          onChange={onSelect}
        />
      </div>
      <div className="px-6 pb-6 pt-3 space-y-3 border-t border-gray-100">
        <Button onClick={onNext} disabled={!budgetMax}>Continue →</Button>
        {onBack && <Button variant="ghost" onClick={onBack}>← Back</Button>}
      </div>
    </>
  )
}

// ── Step 4: Inference summary ─────────────────────────────────────────────────

function Step4Inference({
  infer,
  hasHistory,
  onNext,
  onBack,
}: {
  infer: Record<string, unknown> | null
  hasHistory: boolean
  onNext: () => void
  onBack?: () => void
}) {
  const dietType        = infer?.diet_type        as string | undefined
  const budgetMax       = infer?.weekly_budget_max as number | undefined
  const orderDay        = infer?.preferred_order_day as string | undefined
  const addressLine     = infer?.address_line      as string | undefined
  const pantrySeeds     = (infer?.pantry_seeds     as {item_name: string}[] | undefined) ?? []
  const visibleSeeds    = pantrySeeds.slice(0, 5)
  const overflowCount   = pantrySeeds.length - visibleSeeds.length

  const capDay = (d: string) => d.charAt(0).toUpperCase() + d.slice(1)

  return (
    <div className="bg-white rounded-3xl shadow-xl overflow-hidden flex flex-col flex-1 min-h-0">
      {/* Full-bleed gradient header */}
      <div
        className="px-6 pt-8 pb-6 text-center"
        style={{ background: "linear-gradient(135deg, #1B4332, #2D6A4F)" }}
      >
        <div className="text-4xl mb-3">🔍</div>
        <h2 className="text-xl font-bold text-white mb-1">
          {hasHistory ? "Here's what we already know" : "Here's what we found"}
        </h2>
        <p className="text-sm" style={{ color: "rgba(255,255,255,0.7)" }}>
          {hasHistory
            ? "Pulled from your Swiggy order history"
            : "No previous Swiggy orders yet"}
        </p>
      </div>

      {/* Scrollable card body */}
      <div className="flex-1 overflow-y-auto px-6 pt-5 pb-2 space-y-4">

        {/* Your household — history only */}
        {hasHistory && (dietType || orderDay || budgetMax) && (
          <InfCard title="Your household">
            {dietType && (
              <InfRow
                label="Diet pattern"
                value={dietType.replace(/_/g, " ").replace(/^\w/, c => c.toUpperCase())}
              />
            )}
            {orderDay && (
              <InfRow label="Typical order day" value={capDay(orderDay)} />
            )}
            {budgetMax && (
              <InfRow label="Avg weekly spend" value={`₹${budgetMax.toLocaleString("en-IN")}`} />
            )}
          </InfCard>
        )}

        {/* Your go-to items — history only */}
        {hasHistory && visibleSeeds.length > 0 && (
          <InfCard title="Your go-to items">
            <div className="flex flex-wrap gap-1.5 mt-1">
              {visibleSeeds.map((s) => (
                <span
                  key={s.item_name}
                  className="bg-white border border-gray-200 rounded-lg px-2.5 py-1 text-xs font-medium text-gray-700"
                >
                  {s.item_name}
                </span>
              ))}
              {overflowCount > 0 && (
                <span className="bg-white border border-gray-200 rounded-lg px-2.5 py-1 text-xs font-medium text-gray-500">
                  + {overflowCount} more
                </span>
              )}
            </div>
          </InfCard>
        )}

        {/* Delivery address — always shown if available */}
        {addressLine && (
          <InfCard title="Delivery address">
            <InfRow label={`📍 ${addressLine}`} />
          </InfCard>
        )}

        {/* No-history message */}
        {!hasHistory && (
          <p className="text-sm text-gray-500 text-center py-2 leading-relaxed">
            We'll personalise your basket as you shop.<br />The more you order, the smarter we get.
          </p>
        )}
      </div>

      {/* Pinned CTA footer */}
      <div className="px-6 pb-6 pt-3 space-y-3 border-t border-gray-100">
        <p className="text-xs text-gray-400 text-center">Does this look right?</p>
        <Button onClick={onNext}>✓ Yes, looks good</Button>
        {onBack && <Button variant="ghost" onClick={onBack}>← Back</Button>}
      </div>
    </div>
  )
}

function InfCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-[#F7F8F5] rounded-2xl p-4">
      <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-widest mb-3">{title}</p>
      {children}
    </div>
  )
}

function InfRow({ label, value }: { label: string; value?: string }) {
  return (
    <div className="flex items-start gap-2.5 mb-2.5 last:mb-0">
      <span
        className="mt-1.5 shrink-0 rounded-full"
        style={{ width: 8, height: 8, background: "#D8F3DC" }}
      />
      <span className="text-[13px] font-medium text-gray-700 flex-1">{label}</span>
      {value && (
        <span className="text-[13px] font-semibold" style={{ color: "#2D6A4F" }}>{value}</span>
      )}
    </div>
  )
}

// ── Step 5: WhatsApp number ───────────────────────────────────────────────────

function Step5Phone({
  onSent,
  onBack,
}: {
  onSent: (phone: string) => void
  onBack?: () => void
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
    <form onSubmit={handleSend} className="flex flex-col flex-1 min-h-0">
      <div className="flex-1 overflow-y-auto px-6 pt-2 pb-2 space-y-4">
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
      </div>

      <div className="px-6 pb-6 pt-3 space-y-3 border-t border-gray-100">
        <Button type="submit" loading={loading}>Send code on WhatsApp →</Button>
        {onBack && <Button variant="ghost" onClick={onBack} type="button">← Back</Button>}
      </div>
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
    <form onSubmit={handleVerify} className="flex flex-col flex-1 min-h-0">
      <div className="flex-1 overflow-y-auto px-6 pt-2 pb-2 space-y-5">
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
      </div>

      <div className="px-6 pb-6 pt-3 space-y-3 border-t border-gray-100">
        <Button type="submit" loading={loading} disabled={otp.length < 6}>
          Verify →
        </Button>
      </div>
    </form>
  )
}

// ── Step 6b: Kitchen questions ────────────────────────────────────────────────

function StepKitchenQuestions({
  cookingStyle,
  usesOnionGarlic,
  onChangeCookingStyle,
  onChangeOnionGarlic,
  onNext,
  onBack,
  loading,
}: {
  cookingStyle:        string
  usesOnionGarlic:     boolean | null
  onChangeCookingStyle: (v: string) => void
  onChangeOnionGarlic:  (v: boolean) => void
  onNext:  () => void
  onBack?: () => void
  loading: boolean
}) {
  const cookingOptions = [
    { value: "south_indian", label: "South Indian", emoji: "🍛" },
    { value: "north_indian", label: "North Indian",  emoji: "🫓" },
    { value: "simple_quick", label: "Simple / Quick", emoji: "⚡" },
    { value: "mixed_varied", label: "Mixed / Varied",  emoji: "🌍" },
  ]

  const canContinue = !!cookingStyle && usesOnionGarlic !== null

  return (
    <>
      <div className="flex-1 overflow-y-auto px-6 pt-2 pb-2 space-y-6">
        {/* Cooking style grid */}
        <div>
          <p className="text-sm font-semibold text-gray-700 mb-3">Cooking style</p>
          <div className="grid grid-cols-2 gap-3">
            {cookingOptions.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => onChangeCookingStyle(opt.value)}
                className={`flex flex-col items-center gap-2 py-4 px-3 rounded-2xl border-2 transition-all
                  ${cookingStyle === opt.value
                    ? "border-[#2D6A4F] bg-[#D8F3DC]"
                    : "border-gray-200 bg-white hover:border-gray-300"
                  }`}
              >
                <span className="text-2xl">{opt.emoji}</span>
                <span className={`text-xs font-semibold text-center leading-tight
                  ${cookingStyle === opt.value ? "text-[#1B4332]" : "text-gray-700"}`}>
                  {opt.label}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Onion & garlic toggle */}
        <div>
          <p className="text-sm font-semibold text-gray-700 mb-3">Onion &amp; garlic?</p>
          <div className="flex gap-3">
            {[
              { value: true,  label: "Yes, always",    emoji: "🧅" },
              { value: false, label: "No (Jain/Satvik)", emoji: "🕊️" },
            ].map((opt) => (
              <button
                key={String(opt.value)}
                type="button"
                onClick={() => onChangeOnionGarlic(opt.value)}
                className={`flex-1 flex flex-col items-center gap-2 py-4 px-3 rounded-2xl border-2 transition-all
                  ${usesOnionGarlic === opt.value
                    ? "border-[#2D6A4F] bg-[#D8F3DC]"
                    : "border-gray-200 bg-white hover:border-gray-300"
                  }`}
              >
                <span className="text-2xl">{opt.emoji}</span>
                <span className={`text-xs font-semibold text-center leading-tight
                  ${usesOnionGarlic === opt.value ? "text-[#1B4332]" : "text-gray-700"}`}>
                  {opt.label}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="px-6 pb-6 pt-3 space-y-3 border-t border-gray-100">
        <Button onClick={onNext} disabled={!canContinue} loading={loading}>Continue →</Button>
        {onBack && <Button variant="ghost" onClick={onBack}>← Back</Button>}
      </div>
    </>
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
            Continue →
          </Button>
          <Button variant="secondary" onClick={onNext}>
            Set a weekly schedule instead
          </Button>
          <Button variant="ghost" onClick={onSkip}>
            Skip for now
          </Button>
        </div>

      </div>
    </div>
  )
}

// ── Step 8: All set ───────────────────────────────────────────────────────────

function Step8AllSet({
  onFinish,
  whatsappEnabled,
}: {
  onFinish: (placeNow: boolean) => void
  whatsappEnabled: boolean
}) {
  const [loading, setLoading] = useState(false)

  const howItWorks = whatsappEnabled
    ? [
        { icon: "🧠", text: "PantryPilot plans your basket each week" },
        { icon: "💬", text: "You get it on WhatsApp before anything ships" },
        { icon: "✅", text: "Confirm, edit, or skip — you're always in control" },
      ]
    : [
        { icon: "🧠", text: "PantryPilot plans your basket each week" },
        { icon: "📋", text: "Review it here in the dashboard before anything ships" },
        { icon: "✅", text: "Confirm, edit, or skip — you're always in control" },
      ]

  return (
    <>
      <div className="flex-1 overflow-y-auto px-6 pt-6 pb-2 space-y-5">
        <div className="text-center space-y-2">
          <div className="text-5xl mb-3">🎉</div>
          <h3 className="text-xl font-bold text-gray-900">You&apos;re all set!</h3>
          <p className="text-sm text-gray-500">
            Your first basket is being prepared. We&apos;ll notify you when it&apos;s ready to review.
          </p>
        </div>

        <div className="bg-[#F7F8F5] border border-[#D8F3DC] rounded-2xl p-4 space-y-3">
          <p className="text-xs font-semibold text-[#2D6A4F] uppercase tracking-wide">How it works</p>
          {howItWorks.map((r) => (
            <div key={r.text} className="flex items-center gap-3 text-sm text-gray-700">
              <span className="text-base">{r.icon}</span>
              {r.text}
            </div>
          ))}
        </div>
      </div>

      <div className="px-6 pb-6 pt-3 border-t border-gray-100">
        <Button
          loading={loading}
          onClick={() => { setLoading(true); onFinish(false) }}
        >
          Go to dashboard →
        </Button>
      </div>
    </>
  )
}

// ── Root onboarding page ──────────────────────────────────────────────────────

type StepKey =
  | "household" | "diet" | "budget"   // questionnaire (Flow B only)
  | "inference"                         // always present
  | "kitchen"                           // always present (after OTP or after inference)
  | "phone"     | "otp"                 // WA steps, conditional
  | "allset"                            // always present

const QUESTIONNAIRE_STEP_KEYS: StepKey[] = ["household", "diet", "budget"]

function computeFlow(hasHistory: boolean, whatsappEnabled: boolean): StepKey[] {
  const questionnaire: StepKey[] = hasHistory ? [] : ["household", "diet", "budget"]
  const wa: StepKey[]            = whatsappEnabled ? ["phone", "otp"] : []
  return [...questionnaire, "inference", ...wa, "kitchen", "allset"]
}

const STEP_META: Record<StepKey, { icon: string; title: string; subtitle: (hasHistory: boolean) => string }> = {
  household: { icon: "🏠", title: "Who's in your household?",     subtitle: () => "We'll plan the right quantities for you." },
  diet:      { icon: "🥗", title: "What's your diet preference?", subtitle: () => "We'll filter out items that don't match." },
  budget:    { icon: "💰", title: "What's your weekly budget?",   subtitle: () => "We'll keep your basket within range." },
  inference: {
    icon: "✨",
    title: "Here's what we found",
    subtitle: (h) => h
      ? "Based on your Swiggy order history."
      : "No previous Swiggy orders found. We'll learn your preferences as you shop.",
  },
  kitchen: { icon: "🍳", title: "Tell us about your kitchen", subtitle: () => "We'll tailor your basket to how you actually cook." },
  phone:  { icon: "📱", title: "Connect WhatsApp",           subtitle: () => "We'll send your basket here every week." },
  otp:    { icon: "🔒", title: "Enter verification code",    subtitle: () => "Check your WhatsApp messages." },
  allset: { icon: "🚀", title: "You're ready to go!",        subtitle: () => "" },
}

export default function OnboardPage() {
  const router = useRouter()

  // Flow is empty until inference resolves; null = not yet computed
  const [flow,           setFlow]           = useState<StepKey[] | null>(null)
  const [currentStep,    setCurrentStep]    = useState<StepKey>("household")
  const [hasHistory,     setHasHistory]     = useState(false)
  const [whatsappEnabled, setWhatsappEnabled] = useState(true)

  // Form state
  const [householdType,    setHouseholdType]    = useState("couple")
  const [dietType,         setDietType]         = useState("vegetarian")
  const [budgetMin,        setBudgetMin]        = useState(1500)
  const [budgetMax,        setBudgetMax]        = useState(2500)
  const [phone,            setPhone]            = useState("")
  const [infer,            setInfer]            = useState<Record<string, unknown> | null>(null)
  const [error,            setError]            = useState("")
  const [cookingStyle,     setCookingStyle]     = useState("")
  const [usesOnionGarlic,  setUsesOnionGarlic]  = useState<boolean | null>(null)
  const [kitchenSaving,    setKitchenSaving]    = useState(false)

  // On mount: inference first (sequential), then resume logic
  useEffect(() => {
    let waEnabled = true

    async function bootstrap() {
      // 1. Fetch settings — default true on failure so WA steps are never
      //    accidentally skipped due to a transient settings error
      try {
        const settingsRes = await api.settings.get()
        if (settingsRes.success && settingsRes.data) {
          waEnabled = (settingsRes.data as import("@/lib/api").SettingsResponse).whatsapp_enabled ?? true
          setWhatsappEnabled(waEnabled)
        }
      } catch { /* keep default */ }

      // 2. Run inference — must complete before status so flow is computed first
      let history = false
      try {
        const inferRes = await api.onboard.infer()
        if (inferRes.success && inferRes.data) {
          const d = inferRes.data as Record<string, unknown>
          setInfer(d)
          history = d.has_order_history === true
          setHasHistory(history)
          if (d.diet_type)         setDietType(d.diet_type as string)
          if (d.weekly_budget_min) setBudgetMin(d.weekly_budget_min as number)
          if (d.weekly_budget_max) setBudgetMax(d.weekly_budget_max as number)
        }
      } catch { setInfer(null); /* history stays false — show questionnaire */ }

      const computed = computeFlow(history, waEnabled)
      setFlow(computed)

      // 3. Resume logic — fires after flow is known
      try {
        const statusRes = await api.onboard.status()
        if (!statusRes.success) {
          router.replace("/")
          return
        }
        if (statusRes.data) {
          const d = statusRes.data as Record<string, unknown>
          if (d.onboarding_complete) {
            router.replace("/dashboard")
            return
          }
          if (d.diet_type)        setDietType(d.diet_type as string)
          if (d.weekly_budget_min) setBudgetMin(d.weekly_budget_min as number)
          if (d.weekly_budget_max) setBudgetMax(d.weekly_budget_max as number)
          if (d.household_type)   setHouseholdType(d.household_type as string)
          if (d.whatsapp_number)  setPhone(d.whatsapp_number as string)

          if (d.whatsapp_verified) {
            setCurrentStep("allset")
          } else if (d.profile_saved) {
            setCurrentStep(waEnabled ? "phone" : "allset")
          } else {
            setCurrentStep(computed[0])
          }
        }
      } catch { setCurrentStep(computed[0]) }
    }

    bootstrap()
  }, [])

  function goNext() {
    if (!flow) return
    const idx = flow.indexOf(currentStep)
    if (idx < flow.length - 1) setCurrentStep(flow[idx + 1])
  }

  function goBack() {
    if (!flow) return
    const idx = flow.indexOf(currentStep)
    if (idx > 0) setCurrentStep(flow[idx - 1])
  }

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
      goNext()
    } else {
      setError((res.error as { message?: string })?.message ?? "Could not save profile.")
    }
  }

  async function saveKitchenAndAdvance() {
    setKitchenSaving(true); setError("")
    const res = await api.onboard.saveProfile({
      household_type:    householdType as "solo" | "couple" | "family" | "joint_family",
      member_count:      householdType === "solo" ? 1 : householdType === "couple" ? 2 : householdType === "family" ? 4 : 8,
      diet_type:         dietType as "vegetarian" | "vegan" | "jain" | "non_vegetarian",
      weekly_budget_min: budgetMin,
      weekly_budget_max: budgetMax,
      allergies:         [],
      cooking_style:     cookingStyle,
      uses_onion_garlic: usesOnionGarlic ?? true,
    } as Parameters<typeof api.onboard.saveProfile>[0])
    setKitchenSaving(false)
    if (res.success) {
      goNext()
    } else {
      setError((res.error as { message?: string })?.message ?? "Could not save kitchen preferences.")
    }
  }

  async function handleComplete(placeNow: boolean) {
    setError("")
    const res = await api.onboard.complete({ place_order_now: placeNow })
    if (res.success) {
      router.push("/dashboard")
    } else {
      setError((res.error as { message?: string })?.message ?? "Something went wrong.")
    }
  }

  // Show spinner while flow is being computed (inference in flight)
  if (!flow) {
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

  const meta              = STEP_META[currentStep]
  const questionnaireSteps = flow.filter(s => QUESTIONNAIRE_STEP_KEYS.includes(s))
  const isQuestionnaireStep = QUESTIONNAIRE_STEP_KEYS.includes(currentStep)
  const currentQStep      = questionnaireSteps.indexOf(currentStep) + 1
  const isFirstStep       = currentStep === flow[0]

  return (
    <Shell>
      {/* Logo bar */}
      <div className="flex items-center justify-between px-1 mb-4">
        <div className="flex items-center gap-2 text-white">
          <span className="text-xl">🥦</span>
          <span className="font-bold">PantryPilot</span>
        </div>
        {isQuestionnaireStep && questionnaireSteps.length > 0 && (
          <span className="text-[#D8F3DC] text-xs">
            Step {currentQStep} of {questionnaireSteps.length}
          </span>
        )}
      </div>

      {/* Inference step owns its full shell — rendered outside <Card> */}
      {currentStep === "inference"
        ? (
          <div className="flex-1 min-h-0 flex flex-col">
            <Step4Inference
              infer={infer}
              hasHistory={hasHistory}
              onNext={goNext}
              onBack={isFirstStep ? undefined : goBack}
            />
          </div>
        )
        : (
          <Card className="flex-1 min-h-0 flex flex-col">
            {/* Progress bar — only for questionnaire steps */}
            {isQuestionnaireStep && questionnaireSteps.length > 0 && (
              <StepBar step={currentQStep} total={questionnaireSteps.length} />
            )}

            {/* Section header — not shown for allset (owns its layout) */}
            {currentStep !== "allset" && (
              <div className="px-6 pt-5 pb-2">
                <div className="text-3xl mb-2">{meta.icon}</div>
                <h2 className="text-xl font-bold text-gray-900">{meta.title}</h2>
                {meta.subtitle(hasHistory) && (
                  <p className="text-sm text-gray-500 mt-1">{meta.subtitle(hasHistory)}</p>
                )}
              </div>
            )}

            {error && (
              <div className="px-6 pb-2">
                <Alert type="error" message={error} />
              </div>
            )}

            {currentStep === "household" && (
              <Step1Household
                value={householdType}
                onChange={setHouseholdType}
                onNext={goNext}
              />
            )}

            {currentStep === "diet" && (
              <Step2Diet
                value={dietType}
                onChange={setDietType}
                onNext={goNext}
                onBack={isFirstStep ? undefined : goBack}
              />
            )}

            {currentStep === "budget" && (
              <Step3Budget
                budgetMax={budgetMax}
                onSelect={(min, max) => { setBudgetMin(min); setBudgetMax(max) }}
                onNext={saveProfileAndAdvance}
                onBack={isFirstStep ? undefined : goBack}
              />
            )}

            {currentStep === "phone" && (
              <Step5Phone
                onSent={(p) => { setPhone(p); goNext() }}
                onBack={isFirstStep ? undefined : goBack}
              />
            )}

            {currentStep === "otp" && (
              <Step6Otp
                phone={phone}
                onVerified={goNext}
                onChangeNumber={goBack}
              />
            )}

            {currentStep === "kitchen" && (
              <StepKitchenQuestions
                cookingStyle={cookingStyle}
                usesOnionGarlic={usesOnionGarlic}
                onChangeCookingStyle={setCookingStyle}
                onChangeOnionGarlic={setUsesOnionGarlic}
                onNext={saveKitchenAndAdvance}
                onBack={isFirstStep ? undefined : goBack}
                loading={kitchenSaving}
              />
            )}

            {currentStep === "allset" && (
              <Step8AllSet onFinish={handleComplete} whatsappEnabled={whatsappEnabled} />
            )}
          </Card>
        )
      }
    </Shell>
  )
}
