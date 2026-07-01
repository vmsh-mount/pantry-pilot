"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { api } from "@/lib/api"
import { AppShell, Card, Button, Alert, Spinner, BudgetBar, SubstitutionBanner } from "@/components/ui"

// ── Types ─────────────────────────────────────────────────────────────────────

interface BasketItem {
  item_name:           string
  brand:               string | null
  product_name:        string | null
  sku_id:              string | null
  quantity:            number
  unit:                string
  unit_price:          number
  total_price:         number
  is_substitution:     boolean
  original_item_name?: string
  add_reason?:         string
  category?:           string
}

interface PendingBasket {
  pending:         true
  loop_run_id:     string
  triggered_at:    string
  confirm_sent_at: string | null
  items:           BasketItem[]
  estimated_total: number
  item_count:      number
  budget_max?:     number
}

interface NoPendingBasket {
  pending:      false
  in_progress:  boolean
  run_state?:   string
  triggered_at?: string
  last_failed:  boolean
  next_run_at:  string | null
}

type BasketState = PendingBasket | NoPendingBasket

// ── Category helpers ──────────────────────────────────────────────────────────

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
  if (/\b(rice|atta|flour|dal|lentil|pulse|poha|suji|semolina|maida|bread|roti)\b/.test(n)) return "staples"
  if (/\b(milk|curd|yogurt|paneer|cheese|butter|ghee|cream)\b/.test(n)) return "dairy"
  if (/\b(tomato|onion|potato|carrot|spinach|cabbage|cauliflower|brinjal|peas|beans|gourd|capsicum|cucumber|okra)\b/.test(n)) return "vegetables"
  if (/\b(apple|banana|mango|grape|orange|lemon|pomegranate|watermelon|papaya|guava)\b/.test(n)) return "fruits"
  if (/\b(salt|sugar|oil|mustard|cumin|turmeric|chilli|pepper|masala|spice|haldi|jeera)\b/.test(n)) return "spices"
  if (/\b(biscuit|cookie|snack|chips|popcorn|namkeen|wafer|chocolate)\b/.test(n)) return "snacks"
  if (/\b(tea|coffee|juice|water|soda|drink|cocoa|horlicks|boost|bournvita)\b/.test(n)) return "beverages"
  if (/\b(detergent|soap|shampoo|toothpaste|cleaner|vim|surf|dettol|sanitizer|dishwash)\b/.test(n)) return "cleaning"
  if (/\b(lotion|cream|conditioner|hair oil|face wash|body wash|perfume|deodorant)\b/.test(n)) return "personal"
  return "grocery"
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-IN", {
    weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit",
  })
}

// ── Category group component ──────────────────────────────────────────────────

function CategorySection({ cat, items }: { cat: string; items: BasketItem[] }) {
  const emoji = CATEGORY_EMOJI[cat] ?? "🛒"
  const label = cat.charAt(0).toUpperCase() + cat.slice(1)
  return (
    <div>
      <div className="flex items-center gap-2 px-4 py-2 bg-[#F7F8F5]">
        <span className="text-sm">{emoji}</span>
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{label}</span>
      </div>
      {items.map((item, i) => (
        <div key={i} className="flex items-start gap-3 px-4 py-3 border-b border-gray-50 last:border-0">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-gray-900 leading-snug truncate">
              {item.product_name || item.item_name}
            </p>
            {item.brand && <p className="text-xs text-gray-400 mt-0.5">{item.brand}</p>}
            {item.is_substitution && (
              <span className="inline-block mt-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-50 text-amber-700 border border-amber-200">
                substitution
              </span>
            )}
          </div>
          <div className="text-right shrink-0">
            <p className="text-sm font-semibold text-gray-900">₹{Math.round(item.total_price)}</p>
            <p className="text-xs text-gray-400">
              {item.quantity} {item.unit}
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const router = useRouter()
  const [basket,        setBasket]        = useState<BasketState | null>(null)
  const [loading,       setLoading]       = useState(true)
  const [actionLoading, setActionLoading] = useState<"confirm" | "skip" | "trigger" | null>(null)
  const [error,         setError]         = useState("")
  const [successMsg,    setSuccessMsg]    = useState("")
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    loadBasket()
    return () => stopPolling()
  }, [])

  function startPolling() {
    if (pollRef.current) return
    pollRef.current = setInterval(() => silentRefresh(), 5000)
  }

  function stopPolling() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  async function silentRefresh() {
    const res = await api.basket.pending()
    if (res.success && res.data) {
      const data = res.data as BasketState
      setBasket(data)
      // Stop polling once we leave in_progress state
      if (!data.pending && !(data as NoPendingBasket).in_progress) {
        stopPolling()
      }
    }
  }

  async function loadBasket() {
    setLoading(true); setError("")
    const res = await api.basket.pending()
    setLoading(false)
    if (res.success && res.data) {
      const data = res.data as BasketState
      setBasket(data)
      // Auto-poll if a run is in progress
      if (!data.pending && (data as NoPendingBasket).in_progress) {
        startPolling()
      }
    } else {
      const code = (res.error as { code?: string })?.code
      if (code === "NOT_AUTHENTICATED" || code === "TOKEN_EXPIRED") {
        router.push("/")
      } else if (code === "ONBOARDING_INCOMPLETE") {
        router.push("/onboard")
      } else {
        setError((res.error as { message?: string })?.message ?? "Could not load basket.")
      }
    }
  }

  async function handleConfirm() {
    setActionLoading("confirm"); setError("")
    const res = await api.basket.confirm()
    setActionLoading(null)
    if (res.success) {
      setSuccessMsg("Order placed! You'll get a WhatsApp confirmation shortly. 🎉")
      setBasket(null)
    } else {
      setError((res.error as { message?: string })?.message ?? "Could not confirm order.")
    }
  }

  async function handleSkip() {
    setActionLoading("skip"); setError("")
    const res = await api.basket.skip()
    setActionLoading(null)
    if (res.success) {
      setSuccessMsg("Basket skipped. See you next week!")
      loadBasket()
    } else {
      setError((res.error as { message?: string })?.message ?? "Could not skip basket.")
    }
  }

  async function handleTrigger() {
    setActionLoading("trigger"); setError("")
    const res = await api.basket.trigger()
    setActionLoading(null)
    if (res.success) {
      // Immediately show in-progress state and start polling
      setBasket({ pending: false, in_progress: true, last_failed: false, next_run_at: null })
      startPolling()
    } else {
      setError((res.error as { message?: string })?.message ?? "Could not trigger basket.")
    }
  }

  return (
    <AppShell>
      {/* Logo bar */}
      <div className="flex items-center justify-between px-1 mb-4">
        <div className="flex items-center gap-2 text-white">
          <span className="text-xl">🥦</span>
          <span className="font-bold">PantryPilot</span>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-20 text-white">
          <Spinner size="lg" />
        </div>
      ) : (
        <div className="space-y-4">
          {error && <Alert type="error" message={error} />}

          {successMsg && (
            <div className="bg-[#D8F3DC] border border-[#2D6A4F]/30 text-[#1B4332] text-sm rounded-2xl px-4 py-3">
              {successMsg}
            </div>
          )}

          {basket === null || !basket?.pending ? (
            /* ── No pending basket / in-progress / failed ── */
            (() => {
              const b = basket as NoPendingBasket | null
              if (b?.in_progress) {
                // Run is happening — show animated state, poll silently
                const stateLabel: Record<string, string> = {
                  pending:    "Getting started…",
                  sensing:    "Checking your pantry…",
                  planning:   "Planning your basket…",
                  optimizing: "Optimising & pricing…",
                }
                const label = stateLabel[b.run_state ?? "pending"] ?? "Building your basket…"
                return (
                  <Card>
                    <div className="px-6 py-10 text-center space-y-4">
                      <div className="flex justify-center">
                        <div className="relative">
                          <div className="w-16 h-16 rounded-full bg-[#D8F3DC] flex items-center justify-center text-3xl animate-pulse">
                            🛒
                          </div>
                        </div>
                      </div>
                      <div>
                        <h2 className="text-base font-bold text-gray-900">{label}</h2>
                        <p className="text-sm text-gray-400 mt-1">This takes about a minute</p>
                      </div>
                      <div className="flex justify-center gap-1.5 pt-1">
                        {["sensing","planning","optimizing"].map((s) => (
                          <div
                            key={s}
                            className={`h-1.5 w-8 rounded-full transition-all ${
                              b.run_state === s || (b.run_state === "optimizing" && s !== "sensing")
                                ? "bg-[#2D6A4F]" : "bg-gray-200"
                            }`}
                          />
                        ))}
                      </div>
                    </div>
                  </Card>
                )
              }

              if (b?.last_failed) {
                return (
                  <Card>
                    <div className="px-6 py-8 text-center space-y-4">
                      <div className="text-5xl">⚠️</div>
                      <div>
                        <h2 className="text-lg font-bold text-gray-900">Basket generation failed</h2>
                        <p className="text-sm text-gray-500 mt-1">
                          Something went wrong. This usually fixes itself — try again.
                        </p>
                      </div>
                      <Button
                        onClick={handleTrigger}
                        loading={actionLoading === "trigger"}
                        disabled={!!actionLoading}
                      >
                        Try again
                      </Button>
                    </div>
                  </Card>
                )
              }

              return (
                <Card>
                  <div className="px-6 py-8 text-center space-y-4">
                    <div className="text-5xl">🛒</div>
                    <div>
                      <h2 className="text-lg font-bold text-gray-900">No basket pending</h2>
                      {b?.next_run_at ? (
                        <p className="text-sm text-gray-500 mt-1">
                          Next basket scheduled for{" "}
                          <span className="font-medium text-gray-700">
                            {fmtDate(b.next_run_at)}
                          </span>
                        </p>
                      ) : (
                        <p className="text-sm text-gray-500 mt-1">You can generate one now.</p>
                      )}
                    </div>
                    <Button
                      onClick={handleTrigger}
                      loading={actionLoading === "trigger"}
                      disabled={!!actionLoading}
                    >
                      Generate basket now
                    </Button>
                  </div>
                </Card>
              )
            })()
          ) : (
            /* ── Pending basket ── */
            <>
              <Card>
                {/* Green header */}
                <div className="bg-[#2D6A4F] px-6 py-5 space-y-3">
                  <div className="flex items-start justify-between">
                    <div>
                      <h1 className="text-xl font-bold text-white">Your basket is ready</h1>
                      <p className="text-[#D8F3DC] text-sm mt-0.5">
                        {basket.item_count} items · ₹{Math.round(basket.estimated_total).toLocaleString("en-IN")}
                      </p>
                    </div>
                    <span className="text-3xl">🛒</span>
                  </div>
                  {basket.budget_max && (
                    <BudgetBar
                      spent={basket.estimated_total}
                      budget={basket.budget_max}
                    />
                  )}
                  {basket.confirm_sent_at && (
                    <p className="text-[#D8F3DC] text-xs">
                      WhatsApp sent {fmtDate(basket.confirm_sent_at)}
                    </p>
                  )}
                </div>

                {/* Substitution banner */}
                {basket.items.some((it) => it.is_substitution) && (
                  <div className="px-4 pt-3">
                    <SubstitutionBanner
                      items={basket.items
                        .filter((it) => it.is_substitution)
                        .map((it) => ({ item_name: it.item_name, original_item_name: it.original_item_name }))}
                    />
                  </div>
                )}

                {/* Items grouped by category */}
                {basket.item_count === 0 ? (
                  <div className="px-6 py-8 text-center">
                    <p className="text-3xl mb-2">🤷</p>
                    <p className="text-sm font-medium text-gray-600">No items resolved this week</p>
                    <p className="text-xs text-gray-400 mt-1">
                      Your pantry may be fully stocked, or Instamart had no matches.
                    </p>
                  </div>
                ) : (
                  <div className="divide-y divide-gray-50">
                    {(() => {
                      const grouped: Record<string, BasketItem[]> = {}
                      basket.items.forEach((item) => {
                        const cat = item.category || inferCategory(item.item_name)
                        if (!grouped[cat]) grouped[cat] = []
                        grouped[cat].push(item)
                      })
                      return Object.entries(grouped).map(([cat, items]) => (
                        <CategorySection key={cat} cat={cat} items={items} />
                      ))
                    })()}
                  </div>
                )}

                {/* Total footer — only when items exist */}
                {basket.item_count > 0 && (
                  <div className="px-6 py-4 border-t border-gray-100 flex items-center justify-between">
                    <span className="text-sm font-medium text-gray-600">Estimated total</span>
                    <span className="text-lg font-bold text-gray-900">
                      ₹{Math.round(basket.estimated_total).toLocaleString("en-IN")}
                    </span>
                  </div>
                )}
              </Card>

              {/* Actions */}
              <div className="space-y-3">
                {basket.item_count > 0 && (
                  <Button
                    onClick={handleConfirm}
                    loading={actionLoading === "confirm"}
                    disabled={!!actionLoading}
                  >
                    ✅ Approve &amp; order
                  </Button>
                )}
                <Button
                  variant="secondary"
                  onClick={handleSkip}
                  loading={actionLoading === "skip"}
                  disabled={!!actionLoading}
                >
                  Skip this week
                </Button>
              </div>

              <p className="text-xs text-center text-[#D8F3DC]">
                Generated {fmtDate(basket.triggered_at)}
              </p>
            </>
          )}
        </div>
      )}
    </AppShell>
  )
}
