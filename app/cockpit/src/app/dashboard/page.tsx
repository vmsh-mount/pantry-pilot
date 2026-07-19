"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { api } from "@/lib/api"
import { AppShell, Spinner } from "@/components/ui"

// ── API response shape ────────────────────────────────────────────────────────

interface DashboardData {
  flow: {
    basket_pending: boolean
    in_progress: boolean
    placing_order: boolean
    next_run_at: string | null
  }
  routines: {
    active_count: number
    next_run_at: string | null
  }
  week: {
    week_start: string
    week_end: string
    total_spend: number
    budget_max: number | null
    order_count: number
    total_calories: number | null
    calorie_target: number | null
    total_protein_g: number | null
    protein_target: number | null
    total_fiber_g: number | null
    fiber_target: number | null
    total_sodium_mg: number | null
    sodium_target: number | null
    has_nutrition_data: boolean
  }
  stats: {
    total_orders: number
    avg_order_total: number | null
    last_nutrition: { resolved_items: number; total_items: number } | null
  }
  recent_orders: {
    placed_at: string | null
    preview: string
    extra_count: number
    total: number
    order_id: string
  }[]
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtINR(n: number) {
  return "₹" + Math.round(n).toLocaleString("en-IN")
}

function fmtDay(iso: string | null) {
  if (!iso) return "—"
  return new Date(iso).toLocaleDateString("en-IN", { weekday: "short" })
}

function fmtNextRun(iso: string | null): string {
  if (!iso) return "—"
  const target = new Date(iso)
  const now = new Date()
  const diff = Math.round(
    (new Date(target.getFullYear(), target.getMonth(), target.getDate()).getTime() -
      new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()) /
      86400000
  )
  if (diff < 0) return "Overdue"
  if (diff === 0) return "today"
  if (diff === 1) return "tomorrow"
  return target.toLocaleDateString("en-IN", { weekday: "long" })
}

// ── Dot bar (7 dots, one per day of the week) ─────────────────────────────────

function DotBar({ pct, color }: { pct: number; color: string }) {
  const filled = Math.round(Math.min(1, Math.max(0, pct)) * 7)
  return (
    <div className="flex gap-1 flex-1">
      {Array.from({ length: 7 }, (_, i) => (
        <div
          key={i}
          className="w-2 h-2 rounded-full flex-shrink-0"
          style={{ background: i < filled ? color : "var(--dot-empty, #E5E7EB)" }}
        />
      ))}
    </div>
  )
}

// ── Hero progress bar ─────────────────────────────────────────────────────────

function HeroBar({ pct, color }: { pct: number; color: string }) {
  const w = Math.min(100, Math.round(pct * 100))
  return (
    <div className="h-1.5 rounded-full bg-gray-100 mt-3 overflow-hidden">
      <div className="h-full rounded-full transition-all duration-500" style={{ width: `${w}%`, background: color }} />
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(true)
  const [data, setData] = useState<DashboardData | null>(null)

  useEffect(() => {
    api.dashboard.get().then((res) => {
      setLoading(false)
      if (res.success && res.data) {
        setData(res.data as DashboardData)
      } else {
        const code = (res.error as { code?: string })?.code
        if (code === "NOT_AUTHENTICATED" || code === "TOKEN_EXPIRED") router.push("/")
        else if (code === "ONBOARDING_INCOMPLETE") router.push("/onboard")
      }
    })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <AppShell>
      {/* ── Header ── */}
      <div className="flex items-center justify-between px-1 mb-5">
        <div className="flex items-center gap-2 text-white">
          <span className="text-xl">🥦</span>
          <span className="font-bold text-lg">PantryPilot</span>
        </div>
        <button onClick={() => router.push("/settings")} className="text-[#D8F3DC] text-sm">⚙</button>
      </div>

      {loading || !data ? (
        <div className="flex justify-center py-24 text-white"><Spinner size="lg" /></div>
      ) : (
        <div className="space-y-3">

          {/* ── Alert banner ── */}
          {data.flow.basket_pending && (
            <button
              onClick={() => router.push("/flow")}
              className="w-full flex items-center justify-between bg-[#D8F3DC] rounded-2xl px-4 py-3 text-left"
            >
              <div className="flex items-center gap-3">
                <span className="w-2 h-2 rounded-full bg-[#2D6A4F] flex-shrink-0" />
                <div>
                  <p className="text-[13px] font-bold text-[#1B4332]">Basket ready for review</p>
                  <p className="text-[11px] text-[#2D6A4F] mt-0.5">Your weekly order is planned · Tap to confirm</p>
                </div>
              </div>
              <span className="text-[#2D6A4F] text-sm">›</span>
            </button>
          )}

          {!data.flow.basket_pending && data.flow.placing_order && (
            <div className="flex items-center gap-3 bg-amber-50 rounded-2xl px-4 py-3">
              <span className="w-2 h-2 rounded-full bg-amber-500 flex-shrink-0 animate-pulse" />
              <div>
                <p className="text-[13px] font-bold text-amber-900">Placing your order…</p>
                <p className="text-[11px] text-amber-700 mt-0.5">Sending basket to Swiggy</p>
              </div>
            </div>
          )}

          {!data.flow.basket_pending && !data.flow.placing_order && data.flow.in_progress && (
            <div className="flex items-center gap-3 bg-amber-50 rounded-2xl px-4 py-3">
              <span className="w-2 h-2 rounded-full bg-amber-500 flex-shrink-0 animate-pulse" />
              <div>
                <p className="text-[13px] font-bold text-amber-900">Building your basket…</p>
                <p className="text-[11px] text-amber-700 mt-0.5">Checking pantry · Planning items</p>
              </div>
            </div>
          )}

          {/* ── Action strip ── */}
          <div className="grid grid-cols-3 gap-2">
            <button
              onClick={() => router.push("/flow")}
              className="bg-white rounded-2xl px-2 py-3 text-center"
            >
              <div className="text-xl mb-1">↻</div>
              <p className="text-[12px] font-bold text-gray-800">Flow</p>
              <p className="text-[10px] text-gray-400 mt-0.5 leading-tight">
                {data.flow.basket_pending
                  ? "Review now"
                  : data.flow.placing_order
                  ? "Placing…"
                  : data.flow.in_progress
                  ? "Planning…"
                  : data.flow.next_run_at
                  ? `Next: ${fmtNextRun(data.flow.next_run_at)}`
                  : "Set up"}
              </p>
            </button>
            <button
              onClick={() => router.push("/routines")}
              className="bg-white rounded-2xl px-2 py-3 text-center"
            >
              <div className="text-xl mb-1">📋</div>
              <p className="text-[12px] font-bold text-gray-800">Routines</p>
              <p className="text-[10px] text-gray-400 mt-0.5 leading-tight">
                {data.routines.active_count > 0 ? `${data.routines.active_count} active` : "Set up"}
              </p>
            </button>
            <button
              onClick={() => router.push("/quick")}
              className="bg-white rounded-2xl px-2 py-3 text-center"
            >
              <div className="text-xl mb-1">🛒</div>
              <p className="text-[12px] font-bold text-gray-800">Quick</p>
              <p className="text-[10px] text-gray-400 mt-0.5 leading-tight">Order now</p>
            </button>
          </div>

          {/* ── This week label ── */}
          <p className="text-[11px] font-bold tracking-widest uppercase text-[rgba(216,243,220,0.55)] px-0.5 pt-1">
            This week
          </p>

          {/* ── Hero tiles ── */}
          <div className="grid grid-cols-2 gap-2.5">
            {/* Calories */}
            <div className="bg-white rounded-2xl px-4 pt-3.5 pb-4">
              <div className="text-lg mb-1">🔥</div>
              <p className="text-[22px] font-bold leading-none text-[#C45E18] tabular-nums">
                {data.week.total_calories != null
                  ? Math.round(data.week.total_calories).toLocaleString("en-IN")
                  : "—"}
              </p>
              <p className="text-[11px] text-gray-400 mt-1">
                {data.week.calorie_target
                  ? `of ${Math.round(data.week.calorie_target).toLocaleString("en-IN")} kcal`
                  : "kcal this week"}
              </p>
              {data.week.total_calories != null && data.week.calorie_target && (
                <HeroBar pct={data.week.total_calories / data.week.calorie_target} color="#C45E18" />
              )}
            </div>

            {/* Spend */}
            <div className="bg-white rounded-2xl px-4 pt-3.5 pb-4">
              <div className="text-lg mb-1">💸</div>
              {(() => {
                const spendPct = data.week.budget_max ? data.week.total_spend / data.week.budget_max : 0
                const spendColor = spendPct >= 1 ? "#DC2626" : spendPct >= 0.8 ? "#D97706" : "#2A60A8"
                return (
                  <>
                    <p className="text-[22px] font-bold leading-none tabular-nums" style={{ color: spendColor }}>
                      {fmtINR(data.week.total_spend)}
                    </p>
                    <p className="text-[11px] text-gray-400 mt-1">
                      {data.week.budget_max ? `of ${fmtINR(data.week.budget_max)} budget` : "spent this week"}
                    </p>
                    {data.week.budget_max && <HeroBar pct={spendPct} color={spendColor} />}
                  </>
                )
              })()}
            </div>
          </div>

          {/* ── Stat tiles ── */}
          <div className="grid grid-cols-3 gap-2">
            <div className="bg-white/10 rounded-xl px-2.5 py-3 text-center">
              <p className="text-white font-bold text-[17px] tabular-nums">{data.stats.total_orders}</p>
              <p className="text-[#D8F3DC]/60 text-[10px] mt-0.5 leading-tight">orders placed</p>
            </div>
            <div className="bg-white/10 rounded-xl px-2.5 py-3 text-center">
              <p className="text-white font-bold text-[17px] tabular-nums">
                {data.stats.avg_order_total != null ? fmtINR(data.stats.avg_order_total) : "—"}
              </p>
              <p className="text-[#D8F3DC]/60 text-[10px] mt-0.5 leading-tight">avg order</p>
            </div>
            <div className="bg-white/10 rounded-xl px-2.5 py-3 text-center">
              <p className="text-white font-bold text-[17px] tabular-nums">
                {data.stats.last_nutrition
                  ? `${data.stats.last_nutrition.resolved_items}/${data.stats.last_nutrition.total_items}`
                  : "—"}
              </p>
              <p className="text-[#D8F3DC]/60 text-[10px] mt-0.5 leading-tight">items resolved</p>
            </div>
          </div>

          {/* ── Nutrition section ── */}
          {data.week.has_nutrition_data && (
            <>
              <p className="text-[11px] font-bold tracking-widest uppercase text-[rgba(216,243,220,0.55)] px-0.5 pt-1">
                Nutrition
              </p>
              <div className="bg-white rounded-2xl px-4 py-3 space-y-0">
                {[
                  {
                    label: "Protein",
                    actual: data.week.total_protein_g,
                    target: data.week.protein_target,
                    unit: "g",
                    color: "#2A60A8",
                  },
                  {
                    label: "Fiber",
                    actual: data.week.total_fiber_g,
                    target: data.week.fiber_target,
                    unit: "g",
                    color: "#2A7030",
                  },
                  {
                    label: "Sodium",
                    actual: data.week.total_sodium_mg,
                    target: data.week.sodium_target,
                    unit: "mg",
                    color: "#6038A0",
                  },
                ].map((m, i, arr) => (
                  <div
                    key={m.label}
                    className={`flex items-center gap-2.5 py-2 ${i < arr.length - 1 ? "border-b border-gray-100" : ""}`}
                  >
                    <span className="text-[12px] text-gray-400 w-12 flex-shrink-0">{m.label}</span>
                    <DotBar pct={m.actual != null && m.target ? m.actual / m.target : 0} color={m.color} />
                    <span className="text-[11px] text-gray-400 text-right w-[68px] flex-shrink-0 tabular-nums">
                      {m.actual != null ? `${Math.round(m.actual)}${m.unit}` : "—"}
                      {m.target ? ` / ${Math.round(m.target)}${m.unit}` : ""}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* ── Recent orders ── */}
          {data.recent_orders.length > 0 && (
            <>
              <p className="text-[11px] font-bold tracking-widest uppercase text-[rgba(216,243,220,0.55)] px-0.5 pt-1">
                Recent orders
              </p>
              <div className="bg-white rounded-2xl overflow-hidden">
                {data.recent_orders.map((o, i) => (
                  <button
                    key={o.order_id}
                    onClick={() => router.push("/orders")}
                    className={`w-full flex items-center gap-3 px-4 py-3 text-left ${
                      i < data.recent_orders.length - 1 ? "border-b border-gray-100" : ""
                    }`}
                  >
                    <span className="text-[10px] font-bold uppercase text-gray-300 w-7 flex-shrink-0">
                      {fmtDay(o.placed_at)}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-[13px] font-medium text-gray-800 truncate">
                        {o.preview}
                        {o.extra_count > 0 && (
                          <span className="text-gray-400"> +{o.extra_count}</span>
                        )}
                      </p>
                    </div>
                    <span className="text-[13px] font-bold text-gray-800 flex-shrink-0 tabular-nums">
                      {fmtINR(o.total)}
                    </span>
                    <span className="text-gray-300 text-sm">›</span>
                  </button>
                ))}
              </div>
            </>
          )}


        </div>
      )}
    </AppShell>
  )
}
