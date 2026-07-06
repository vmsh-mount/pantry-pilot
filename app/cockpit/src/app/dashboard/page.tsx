"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { api, type RunSummary, type RunsListResponse, type SettingsResponse } from "@/lib/api"
import { AppShell, Card, Button, Alert, Spinner, BudgetBar, SubstitutionBanner } from "@/components/ui"
import { ItemSearchDropdown, type SearchProduct } from "@/components/basket/ItemSearchDropdown"

// ── Types ─────────────────────────────────────────────────────────────────────

interface BasketItem {
  id:                  string
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
  added_by?:           string
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

function CategorySection({
  cat, items, onRemove, removingIds,
}: {
  cat:         string
  items:       BasketItem[]
  onRemove:    (id: string, name: string) => void
  removingIds: Set<string>
}) {
  const emoji = CATEGORY_EMOJI[cat] ?? "🛒"
  const label = cat.charAt(0).toUpperCase() + cat.slice(1)
  return (
    <div>
      <div className="flex items-center gap-2 px-4 py-2 bg-[#F7F8F5]">
        <span className="text-sm">{emoji}</span>
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{label}</span>
      </div>
      {items.map((item) => (
        <div key={item.id} className="flex items-start gap-3 px-4 py-3 border-b border-gray-50 last:border-0">
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
          <div className="flex items-center gap-2 shrink-0">
            <div className="text-right">
              <p className="text-sm font-semibold text-gray-900">₹{Math.round(item.total_price)}</p>
              <p className="text-xs text-gray-400">
                {item.quantity} {item.unit}
              </p>
            </div>
            <button
              onClick={() => onRemove(item.id, item.item_name)}
              disabled={removingIds.has(item.id)}
              className="ml-1 w-6 h-6 flex items-center justify-center rounded-full text-gray-300 hover:text-red-400 hover:bg-red-50 transition-colors disabled:opacity-30"
              aria-label={`Remove ${item.item_name}`}
            >
              {removingIds.has(item.id) ? (
                <span className="w-3 h-3 border border-gray-300 border-t-transparent rounded-full animate-spin" />
              ) : (
                <span className="text-xs leading-none">✕</span>
              )}
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Status helpers ────────────────────────────────────────────────────────────

const ACTIVE_STATES = new Set(["pending", "sensing", "planning", "optimizing", "confirmed", "placing"])

function RunBadge({ state }: { state: string }) {
  const isActive = ACTIVE_STATES.has(state)
  const styles: Record<string, string> = {
    in_progress:           "bg-amber-50 text-amber-700",
    awaiting_confirmation: "bg-purple-50 text-purple-700",
    completed:             "bg-[#D8F3DC] text-[#2D6A4F]",
    failed:                "bg-red-50 text-red-600",
    skipped:               "bg-gray-100 text-gray-500",
  }
  const badge = isActive ? "in_progress" : state
  const label: Record<string, string> = {
    in_progress:           "In progress",
    awaiting_confirmation: "Awaiting you",
    completed:             "Completed",
    failed:                "Failed",
    skipped:               "Skipped",
  }
  return (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${styles[badge] ?? "bg-gray-100 text-gray-500"}`}>
      {label[badge] ?? badge}
    </span>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const router = useRouter()
  const [basket,        setBasket]        = useState<BasketState | null>(null)
  const [runsData,      setRunsData]      = useState<RunsListResponse | null>(null)
  const [dryRun,           setDryRun]           = useState(false)
  const [whatsappEnabled,  setWhatsappEnabled]  = useState(true)
  const [loading,       setLoading]       = useState(true)
  const [actionLoading, setActionLoading] = useState<"confirm" | "skip" | "trigger" | null>(null)
  const [error,         setError]         = useState("")
  const [successMsg,    setSuccessMsg]    = useState("")
  const [removingIds,   setRemovingIds]   = useState<Set<string>>(new Set())
  const [addingItem,    setAddingItem]    = useState(false)
  const [editSummary,   setEditSummary]   = useState({ removed: 0, added: 0 })
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    loadAll()
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
      if (!data.pending && !(data as NoPendingBasket).in_progress) {
        stopPolling()
        // Refresh runs list when run completes
        loadRuns()
      }
    }
  }

  async function loadRuns() {
    const res = await api.runs.list({ limit: 4 })
    if (res.success && res.data) {
      setRunsData(res.data as RunsListResponse)
    }
  }

  async function loadAll() {
    setLoading(true); setError("")
    loadRuns().catch(() => {})
    const [basketRes, settingsRes] = await Promise.all([
      api.basket.pending(),
      api.settings.get(),
    ])
    if (settingsRes.success && settingsRes.data) {
      const s = settingsRes.data as SettingsResponse
      setDryRun(s.dry_run ?? false)
      setWhatsappEnabled(s.whatsapp_enabled ?? true)
    }
    setLoading(false)
    if (basketRes.success && basketRes.data) {
      const data = basketRes.data as BasketState
      setBasket(data)
      if (!data.pending && (data as NoPendingBasket).in_progress) {
        startPolling()
      }
    } else {
      const code = (basketRes.error as { code?: string })?.code
      if (code === "NOT_AUTHENTICATED" || code === "TOKEN_EXPIRED") {
        router.push("/")
      } else if (code === "ONBOARDING_INCOMPLETE") {
        router.push("/onboard")
      } else {
        setError((basketRes.error as { message?: string })?.message ?? "Could not load basket.")
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
      loadRuns()
    } else {
      setError((res.error as { message?: string })?.message ?? "Could not skip basket.")
    }
  }

  async function handleTrigger() {
    setActionLoading("trigger"); setError("")
    const res = await api.basket.trigger()
    setActionLoading(null)
    if (res.success) {
      setBasket({ pending: false, in_progress: true, last_failed: false, next_run_at: null })
      startPolling()
      loadRuns()
    } else {
      setError((res.error as { message?: string })?.message ?? "Could not trigger basket.")
    }
  }

  function handleTokenExpired() {
    router.push("/reauth")
  }

  async function handleRemoveItem(itemId: string, itemName: string) {
    if (!(basket as PendingBasket)?.items) return
    setRemovingIds((prev) => new Set(prev).add(itemId))
    setError("")

    // Optimistic update
    const prev = basket as PendingBasket
    const newItems = prev.items.filter((i) => i.id !== itemId)
    const newTotal = newItems.reduce((s, i) => s + i.total_price, 0)
    setBasket({ ...prev, items: newItems, estimated_total: newTotal, item_count: newItems.length })
    setEditSummary((s) => ({ ...s, removed: s.removed + 1 }))

    try {
      const res = await api.basket.removeItem(itemId)
      setRemovingIds((prev) => { const n = new Set(prev); n.delete(itemId); return n })

      if (!res.success) {
        const code = (res.error as { code?: string })?.code
        if (code === "TOKEN_EXPIRED") { handleTokenExpired(); return }
        setBasket(prev)
        setEditSummary((s) => ({ ...s, removed: Math.max(0, s.removed - 1) }))
        setError(`Could not remove ${itemName}.`)
      } else {
        const data = res.data as { estimated_total: number; item_count: number }
        setBasket((cur) => cur?.pending ? { ...cur as PendingBasket, estimated_total: data.estimated_total, item_count: data.item_count } : cur)
      }
    } catch {
      setRemovingIds((prev) => { const n = new Set(prev); n.delete(itemId); return n })
      setBasket(prev)
      setEditSummary((s) => ({ ...s, removed: Math.max(0, s.removed - 1) }))
      setError(`Could not remove ${itemName}. Please try again.`)
    }
  }

  const handleSearch = useCallback(async (q: string): Promise<SearchProduct[]> => {
    try {
      const res = await api.basket.searchItems(q)
      if (!res.success) {
        const code = (res.error as { code?: string })?.code
        if (code === "TOKEN_EXPIRED") { router.push("/reauth"); return [] }
        setError("Search failed. Please try again.")
        return []
      }
      return (res.data as { results: SearchProduct[] })?.results ?? []
    } catch {
      setError("Search failed. Check your connection and try again.")
      return []
    }
  }, [router])

  async function handleAddItem(product: SearchProduct) {
    setAddingItem(true); setError("")
    try {
      const res = await api.basket.addItem({
        swiggy_product_id: product.swiggy_product_id,
        name:      product.name,
        price:     product.price,
        image_url: product.image_url,
        brand:     product.brand,
      })
      if (!res.success) {
        const code = (res.error as { code?: string })?.code
        if (code === "TOKEN_EXPIRED") { handleTokenExpired(); return }
        setError(`Could not add ${product.name}.`)
        return
      }
      const data = res.data as { item: BasketItem; estimated_total: number; item_count: number }
      setBasket((cur) => {
        if (!cur?.pending) return cur
        const p = cur as PendingBasket
        return { ...p, items: [...p.items, data.item], estimated_total: data.estimated_total, item_count: data.item_count }
      })
      setEditSummary((s) => ({ ...s, added: s.added + 1 }))
    } catch {
      setError(`Could not add ${product.name}. Please try again.`)
    } finally {
      setAddingItem(false)
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
        <button onClick={() => router.push("/runs")} className="text-[#D8F3DC] text-xs">
          Run history →
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-20 text-white">
          <Spinner size="lg" />
        </div>
      ) : (
        <div className="space-y-4">
          {error && <Alert type="error" message={error} />}

          {dryRun && (
            <div className="rounded-2xl px-4 py-3 bg-amber-50 border border-amber-300 flex items-center gap-2">
              <span className="text-amber-600 text-base">⚠</span>
              <p className="text-xs font-medium text-amber-800">
                Test mode — orders won&apos;t be placed on Swiggy
              </p>
            </div>
          )}

          {!whatsappEnabled && (
            <div className="rounded-2xl px-4 py-3 bg-slate-100 border border-slate-300 flex items-center gap-2">
              <span className="text-slate-500 text-base">ℹ</span>
              <p className="text-xs font-medium text-slate-700">
                WhatsApp off — confirm orders via this dashboard
              </p>
            </div>
          )}

          {/* ── Next run card + stats ── */}
          {runsData && (
            <>
              {/* Next run / guard rail */}
              {(() => {
                const isRunActive = basket && !basket.pending && (basket as NoPendingBasket).in_progress
                const nextRunAt   = runsData.next_run_at
                return (
                  <div className={`rounded-2xl px-4 py-3 flex items-center justify-between gap-3 ${
                    isRunActive
                      ? "bg-amber-50 border border-amber-200"
                      : "bg-white/10 border border-white/20"
                  }`}>
                    <div>
                      <p className={`text-xs font-medium ${isRunActive ? "text-amber-700" : "text-[#D8F3DC]"}`}>
                        {isRunActive ? "A run is already in progress" : "Next run"}
                      </p>
                      {!isRunActive && nextRunAt && (
                        <p className="text-white text-sm font-semibold mt-0.5">
                          {new Date(nextRunAt).toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "short" })}
                        </p>
                      )}
                      {!isRunActive && !nextRunAt && (
                        <p className="text-[#D8F3DC] text-sm mt-0.5">Not scheduled</p>
                      )}
                    </div>
                    {!isRunActive && (
                      <button
                        onClick={handleTrigger}
                        disabled={!!actionLoading}
                        className="shrink-0 px-3 py-1.5 rounded-xl bg-white text-[#2D6A4F] text-xs font-semibold disabled:opacity-50"
                      >
                        {actionLoading === "trigger" ? "…" : "Plan now"}
                      </button>
                    )}
                  </div>
                )
              })()}

              {/* Stats chips */}
              {(runsData.stats.total_runs > 0) && (
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { label: "Runs", value: String(runsData.stats.total_runs) },
                    { label: "Last order", value: runsData.stats.last_order_total != null ? `₹${Math.round(runsData.stats.last_order_total).toLocaleString("en-IN")}` : "—" },
                    { label: "Weekly avg", value: runsData.stats.avg_order_total != null ? `₹${Math.round(runsData.stats.avg_order_total).toLocaleString("en-IN")}` : "—" },
                  ].map((s) => (
                    <div key={s.label} className="bg-white/10 rounded-2xl px-3 py-2 text-center">
                      <p className="text-white font-semibold text-sm">{s.value}</p>
                      <p className="text-[#D8F3DC] text-[10px] mt-0.5">{s.label}</p>
                    </div>
                  ))}
                </div>
              )}

              {/* Recent runs */}
              {runsData.runs.length > 0 && (
                <Card>
                  <div className="px-4 pt-3 pb-1 flex items-center justify-between">
                    <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Recent runs</p>
                    <button onClick={() => router.push("/runs")} className="text-xs text-[#2D6A4F] font-medium">
                      See all →
                    </button>
                  </div>
                  <div className="divide-y divide-gray-50">
                    {runsData.runs.map((run: RunSummary) => (
                      <div key={run.id} className="flex items-center justify-between px-4 py-2.5">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-xs text-gray-500">
                            {new Date(run.triggered_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}
                          </span>
                          <RunBadge state={run.state} />
                        </div>
                        <span className="text-xs text-gray-400">
                          {run.total_price != null ? `₹${Math.round(run.total_price).toLocaleString("en-IN")}` : run.item_count > 0 ? `${run.item_count} items` : ""}
                        </span>
                      </div>
                    ))}
                  </div>
                </Card>
              )}
            </>
          )}

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
                const stateLabel: Record<string, string> = {
                  pending:    "Queued…",
                  sensing:    "Checking your pantry…",
                  planning:   "Planning your basket…",
                  optimizing: "Optimising & pricing…",
                  confirmed:  "Placing order…",
                  placing:    "Placing order…",
                }
                const pipelineStages = ["sensing", "planning", "optimizing"]
                const activeStage    = pipelineStages.indexOf(b.run_state ?? "")
                const showBar        = activeStage !== -1
                const label          = stateLabel[b.run_state ?? "pending"] ?? "Building your basket…"
                return (
                  <Card>
                    <div className="px-6 py-10 text-center space-y-4">
                      <div className="w-16 h-16 rounded-full bg-[#D8F3DC] flex items-center justify-center text-3xl animate-pulse mx-auto">
                        🛒
                      </div>
                      <div>
                        <h2 className="text-base font-bold text-gray-900">{label}</h2>
                        <p className="text-sm text-gray-400 mt-1">This takes about a minute</p>
                      </div>
                      {showBar && (
                        <div className="flex justify-center gap-1.5 pt-1">
                          {pipelineStages.map((s, i) => (
                            <div
                              key={s}
                              className={`h-1.5 w-8 rounded-full transition-all ${i <= activeStage ? "bg-[#2D6A4F]" : "bg-gray-200"}`}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  </Card>
                )
              }

              if (b?.last_failed) {
                return (
                  <Card>
                    <div className="px-6 py-8 text-center space-y-3">
                      <div className="text-5xl">⚠️</div>
                      <h2 className="text-lg font-bold text-gray-900">Last run failed</h2>
                      <p className="text-sm text-gray-500">Use "Plan now" above to try again.</p>
                    </div>
                  </Card>
                )
              }

              return (
                <Card>
                  <div className="px-6 py-8 text-center space-y-2">
                    <div className="text-5xl">🛒</div>
                    <h2 className="text-lg font-bold text-gray-900">No basket pending</h2>
                    <p className="text-sm text-gray-500">Use "Plan now" above to generate your basket.</p>
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
                        {basket.item_count} item{basket.item_count !== 1 ? "s" : ""} · ₹{Math.round(basket.estimated_total).toLocaleString("en-IN")}
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

                {/* Edit change summary banner */}
                {(editSummary.removed > 0 || editSummary.added > 0) && (
                  <div className="mx-4 mt-3 px-3 py-2 rounded-xl bg-[#D8F3DC] border border-[#2D6A4F]/20 flex items-center justify-between">
                    <p className="text-xs font-medium text-[#1B4332]">
                      {[
                        editSummary.removed > 0 && `Removed ${editSummary.removed}`,
                        editSummary.added   > 0 && `Added ${editSummary.added}`,
                      ].filter(Boolean).join(" · ")}
                    </p>
                    <button
                      onClick={() => setEditSummary({ removed: 0, added: 0 })}
                      className="text-[#2D6A4F] text-xs ml-2"
                    >
                      ✕
                    </button>
                  </div>
                )}

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

                {/* Items grouped by category — or empty state */}
                {basket.item_count === 0 ? (
                  <div className="px-6 py-8 text-center">
                    <p className="text-3xl mb-2">🛒</p>
                    <p className="text-sm font-medium text-gray-600">Basket is empty</p>
                    <p className="text-xs text-gray-400 mt-1">
                      Add an item below, or skip this week.
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
                        <CategorySection
                          key={cat}
                          cat={cat}
                          items={items}
                          onRemove={handleRemoveItem}
                          removingIds={removingIds}
                        />
                      ))
                    })()}
                  </div>
                )}

                {/* Add item search */}
                <div className="px-4 py-3 border-t border-gray-100">
                  <ItemSearchDropdown
                    onSearch={handleSearch}
                    onSelect={handleAddItem}
                    disabled={addingItem}
                  />
                </div>

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
                {basket.item_count > 0 ? (
                  <Button
                    onClick={handleConfirm}
                    loading={actionLoading === "confirm"}
                    disabled={!!actionLoading || addingItem}
                  >
                    ✅ Approve &amp; order
                  </Button>
                ) : (
                  <div className="px-4 py-3 rounded-2xl bg-amber-50 border border-amber-200 text-center">
                    <p className="text-sm font-medium text-amber-800">Nothing left — skip this week?</p>
                  </div>
                )}
                <Button
                  variant="secondary"
                  onClick={handleSkip}
                  loading={actionLoading === "skip"}
                  disabled={!!actionLoading || addingItem}
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
