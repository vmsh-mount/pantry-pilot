"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { api } from "@/lib/api"
import { BottomNav, Spinner } from "@/components/ui"

// ── Types ─────────────────────────────────────────────────────────────────────

interface DashboardData {
  flow: {
    basket_pending: boolean
    in_progress:    boolean
    placing_order:  boolean
    next_run_at:    string | null
  }
  routines: {
    active_count: number
    next_run_at:  string | null
  }
  week: {
    week_start:         string
    week_end:           string
    total_spend:        number
    budget_max:         number | null
    order_count:        number
    total_calories:     number | null
    calorie_target:     number | null
    total_protein_g:    number | null
    protein_target:     number | null
    total_fiber_g:      number | null
    fiber_target:       number | null
    total_sodium_mg:    number | null
    sodium_target:      number | null
    has_nutrition_data: boolean
  }
  stats: {
    total_orders:    number
    avg_order_total: number | null
    last_nutrition:  { resolved_items: number; total_items: number } | null
  }
  recent_orders: {
    placed_at:   string | null
    preview:     string
    extra_count: number
    total:       number
    order_id:    string
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
  if (!iso) return "Set up"
  const target = new Date(iso)
  const now    = new Date()
  const diff   = Math.round(
    (new Date(target.getFullYear(), target.getMonth(), target.getDate()).getTime() -
     new Date(now.getFullYear(),    now.getMonth(),    now.getDate()).getTime()) /
    86400000
  )
  if (diff < 0)   return "Overdue"
  if (diff === 0) return "Today"
  if (diff === 1) return "Tomorrow"
  return `Next: ${target.toLocaleDateString("en-IN", { weekday: "short" })}`
}

function fmtNut(n: number | null, target: number | null, unit: string): string {
  if (n === null || target === null) return "—"
  if (unit === "mg") {
    const a = (n      / 1000).toFixed(1).replace(/\.0$/, "")
    const t = (target / 1000).toFixed(1).replace(/\.0$/, "")
    return `${a}k / ${t}k`
  }
  return `${Math.round(n)} / ${Math.round(target)}${unit}`
}

// ── Inline SVG icons (Tabler stroke style) ────────────────────────────────────

function IconRefresh({ size = 18, color = "currentColor" }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path stroke="none" d="M0 0h24v24H0z" fill="none" />
      <path d="M20 11a8.1 8.1 0 0 0 -15.5 -2m-.5 -4v4h4" />
      <path d="M4 13a8.1 8.1 0 0 0 15.5 2m.5 4v-4h-4" />
    </svg>
  )
}

function IconCalendar({ size = 18, color = "currentColor" }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path stroke="none" d="M0 0h24v24H0z" fill="none" />
      <path d="M4 7a2 2 0 0 1 2 -2h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-12z" />
      <path d="M16 3v4" /><path d="M8 3v4" /><path d="M4 11h16" />
      <path d="M11 15h1" /><path d="M12 15v3" />
    </svg>
  )
}

function IconPlus({ size = 18, color = "currentColor" }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path stroke="none" d="M0 0h24v24H0z" fill="none" />
      <path d="M12 5l0 14" /><path d="M5 12l14 0" />
    </svg>
  )
}

function IconSettings({ size = 18, color = "currentColor" }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path stroke="none" d="M0 0h24v24H0z" fill="none" />
      <path d="M10.325 4.317c.426 -1.756 2.924 -1.756 3.35 0a1.724 1.724 0 0 0 2.573 1.066c1.543 -.94 3.31 .826 2.37 2.37a1.724 1.724 0 0 0 1.065 2.572c1.756 .426 1.756 2.924 0 3.35a1.724 1.724 0 0 0 -1.066 2.573c.94 1.543 -.826 3.31 -2.37 2.37a1.724 1.724 0 0 0 -2.572 1.065c-.426 1.756 -2.924 1.756 -3.35 0a1.724 1.724 0 0 0 -2.573 -1.066c-1.543 .94 -3.31 -.826 -2.37 -2.37a1.724 1.724 0 0 0 -1.065 -2.572c-1.756 -.426 -1.756 -2.924 0 -3.35a1.724 1.724 0 0 0 1.066 -2.573c-.94 -1.543 .826 -3.31 2.37 -2.37c1 .608 2.296 .07 2.572 -1.065z" />
      <path d="M9 12a3 3 0 1 0 6 0a3 3 0 0 0 -6 0" />
    </svg>
  )
}

function IconFlame({ size = 14, color = "currentColor" }: { size?: number; color?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path stroke="none" d="M0 0h24v24H0z" fill="none" />
      <path d="M12 12c2 -2.96 0 -7 -1 -8c0 3.038 -1.773 4.741 -3 6c-1.226 1.26 -2 3.24 -2 5a6 6 0 0 0 12 0c0 -1.532 -1.056 -3.94 -2 -5c-1.786 3 -2.791 3 -4 2z" />
    </svg>
  )
}

// ── Dot bar (7 × 9 px dots) ───────────────────────────────────────────────────

function DotBar({ pct, color }: { pct: number; color: string }) {
  const filled = Math.round(Math.min(1, Math.max(0, pct)) * 7)
  return (
    <div className="flex gap-1 flex-1">
      {Array.from({ length: 7 }, (_, i) => (
        <div
          key={i}
          className="w-2.5 h-2.5 rounded-full flex-shrink-0"
          style={{ background: i < filled ? color : "rgba(45,106,79,0.13)" }}
        />
      ))}
    </div>
  )
}

// ── Nutrition rows config ─────────────────────────────────────────────────────

const NUTRITION_ROWS = [
  {
    label:      "Protein",
    dotColor:   "#4A7FA5",
    getActual:  (w: DashboardData["week"]) => w.total_protein_g,
    getTarget:  (w: DashboardData["week"]) => w.protein_target,
    unit:       "g",
    statusGood: (pct: number) => pct >= 0.7,
  },
  {
    label:      "Fiber",
    dotColor:   "#2D6A4F",
    getActual:  (w: DashboardData["week"]) => w.total_fiber_g,
    getTarget:  (w: DashboardData["week"]) => w.fiber_target,
    unit:       "g",
    statusGood: (pct: number) => pct >= 0.7,
  },
  {
    label:      "Sodium",
    dotColor:   "#8B6336",
    getActual:  (w: DashboardData["week"]) => w.total_sodium_mg,
    getTarget:  (w: DashboardData["week"]) => w.sodium_target,
    unit:       "mg",
    statusGood: (pct: number) => pct <= 1.0,
  },
]

// ── Page ──────────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(true)
  const [data, setData]       = useState<DashboardData | null>(null)

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

  if (loading || !data) {
    return (
      <main className="min-h-screen bg-[#F4F4F4] flex items-center justify-center">
        <Spinner size="lg" />
        <BottomNav />
      </main>
    )
  }

  const spendPct = data.week.budget_max
    ? Math.min(1, data.week.total_spend / data.week.budget_max)
    : 0

  const flowSubLabel =
    data.flow.basket_pending ? "Review now"  :
    data.flow.placing_order  ? "Placing…"    :
    data.flow.in_progress    ? "Planning…"   :
    fmtNextRun(data.flow.next_run_at)

  // white-at-opacity helper for hero section
  const wa = (a: number) => `rgba(255,255,255,${a})`
  const HD = "0.5px solid rgba(255,255,255,0.12)" // hero hairline divider

  return (
    <main className="min-h-screen bg-[#F4F4F4] flex flex-col items-center">

      {/* ── GREEN HERO ────────────────────────────────────────────────────────── */}
      <div className="w-full bg-[#2D6A4F]">
        <div className="w-full max-w-[390px] mx-auto px-4 pt-6 pb-6">

          {/* Logo bar */}
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-2">
              <span className="text-xl">🥦</span>
              <span className="text-[17px] font-bold text-white">PantryPilot</span>
            </div>
            <button onClick={() => router.push("/settings")} aria-label="Settings" style={{ opacity: 0.35 }}>
              <IconSettings size={20} color="white" />
            </button>
          </div>

          {/* Alert: basket pending */}
          {data.flow.basket_pending && (
            <button
              onClick={() => router.push("/flow")}
              className="w-full flex items-center gap-2.5 rounded-xl px-3 py-2 mb-4 text-left"
              style={{ background: wa(0.13), border: `0.5px solid ${wa(0.18)}` }}
            >
              <span className="w-[7px] h-[7px] rounded-full flex-shrink-0" style={{ background: wa(0.8) }} />
              <div className="flex-1">
                <p className="text-[13px] font-bold" style={{ color: wa(0.95) }}>Basket ready for review</p>
                <p className="text-[11px] mt-0.5" style={{ color: wa(0.6) }}>Tap to confirm your weekly order</p>
              </div>
              <span className="text-sm" style={{ color: wa(0.5) }}>›</span>
            </button>
          )}

          {/* Alert: in-progress / placing */}
          {!data.flow.basket_pending && (data.flow.placing_order || data.flow.in_progress) && (
            <div
              className="flex items-center gap-2.5 rounded-xl px-3 py-2 mb-4"
              style={{ background: "rgba(245,158,11,0.2)", border: `0.5px solid ${wa(0.18)}` }}
            >
              <span className="w-[7px] h-[7px] rounded-full bg-amber-400 flex-shrink-0 animate-pulse" />
              <div>
                <p className="text-[13px] font-bold" style={{ color: wa(0.95) }}>
                  {data.flow.placing_order ? "Placing your order…" : "Building your basket…"}
                </p>
                <p className="text-[11px] mt-0.5" style={{ color: wa(0.6) }}>
                  {data.flow.placing_order ? "Sending basket to Swiggy" : "Checking pantry · Planning items"}
                </p>
              </div>
            </div>
          )}

          {/* THIS WEEK */}
          <p className="text-[11px] font-bold uppercase tracking-widest mb-1.5" style={{ color: wa(0.45) }}>
            This week
          </p>

          {/* Spend number */}
          <p className="text-5xl font-black text-white tabular-nums leading-none" style={{ letterSpacing: "-2px" }}>
            {fmtINR(data.week.total_spend)}
          </p>
          <p className="text-[13px] mt-1" style={{ color: wa(0.5) }}>
            {data.week.budget_max ? `of ${fmtINR(data.week.budget_max)} budget` : "spent this week"}
          </p>

          {/* Progress bar */}
          {data.week.budget_max && (
            <div className="h-[3px] rounded-full mt-3" style={{ background: wa(0.15) }}>
              <div
                className="h-full rounded-full transition-all duration-500"
                style={{ width: `${Math.round(spendPct * 100)}%`, background: wa(0.72) }}
              />
            </div>
          )}

          {/* Calorie badge */}
          {data.week.has_nutrition_data && data.week.total_calories != null && (
            <div className="flex items-center gap-1.5 mt-3">
              <IconFlame size={15} color="#FF6B00" />
              <span className="text-[13px] font-bold" style={{ color: "#FF6B00" }}>
                {Math.round(data.week.total_calories).toLocaleString("en-IN")}
                {data.week.calorie_target && (
                  <span style={{ color: "rgba(255,107,0,0.55)", fontWeight: 400 }}>
                    {" / "}{Math.round(data.week.calorie_target).toLocaleString("en-IN")} kcal
                  </span>
                )}
              </span>
            </div>
          )}

          {/* Stats footer */}
          <div className="grid grid-cols-3 mt-4 pt-3.5" style={{ borderTop: HD }}>
            <div>
              <p className="text-[18px] font-extrabold tabular-nums" style={{ color: wa(0.9) }}>
                {data.stats.total_orders}
              </p>
              <p className="text-[11px] mt-0.5" style={{ color: wa(0.4) }}>orders placed</p>
            </div>
            <div style={{ borderLeft: HD, paddingLeft: "14px" }}>
              <p className="text-[18px] font-extrabold tabular-nums" style={{ color: wa(0.9) }}>
                {data.stats.avg_order_total != null ? fmtINR(data.stats.avg_order_total) : "—"}
              </p>
              <p className="text-[11px] mt-0.5" style={{ color: wa(0.4) }}>avg order</p>
            </div>
            <div style={{ borderLeft: HD, paddingLeft: "14px" }}>
              <p className="text-[18px] font-extrabold tabular-nums" style={{ color: wa(0.9) }}>
                {data.stats.last_nutrition
                  ? `${data.stats.last_nutrition.resolved_items}/${data.stats.last_nutrition.total_items}`
                  : "—"}
              </p>
              <p className="text-[11px] mt-0.5" style={{ color: wa(0.4) }}>items resolved</p>
            </div>
          </div>

        </div>
      </div>

      {/* ── GRAY BODY ─────────────────────────────────────────────────────────── */}
      <div className="w-full max-w-[390px] mx-auto px-3 pt-3 pb-28 space-y-3">

        {/* Action strip */}
        <div
          className="grid grid-cols-3 bg-white rounded-xl overflow-hidden"
          style={{ border: "1px solid rgba(0,0,0,0.06)" }}
        >
          {[
            { label: "Flow",     Icon: IconRefresh,  sub: flowSubLabel,        href: "/flow"     },
            { label: "Routines", Icon: IconCalendar, sub: data.routines.active_count > 0 ? `${data.routines.active_count} active` : "Set up", href: "/routines" },
            { label: "Quick",    Icon: IconPlus,     sub: "Order now",          href: "/quick"    },
          ].map((btn, i) => (
            <button
              key={btn.label}
              onClick={() => router.push(btn.href)}
              className="flex flex-col items-center py-3.5 px-1"
              style={i > 0 ? { borderLeft: "1px solid rgba(0,0,0,0.07)" } : undefined}
            >
              <btn.Icon size={21} color="#2D6A4F" />
              <span className="text-[13px] font-bold text-[#1C1C1E] mt-1.5">{btn.label}</span>
              <span className="text-[11px] text-[#8E8E93] mt-0.5">{btn.sub}</span>
            </button>
          ))}
        </div>

        {/* Nutrition */}
        {data.week.has_nutrition_data && (
          <>
            <p className="text-[11px] font-extrabold uppercase tracking-widest px-0.5 pt-1" style={{ color: "#5A5A5F" }}>
              Nutrition
            </p>
            <div
              className="rounded-xl overflow-hidden"
              style={{ background: "#F4FBF7", border: "0.5px solid rgba(45,106,79,0.14)" }}
            >
              {NUTRITION_ROWS.map((row, i) => {
                const actual      = row.getActual(data.week)
                const target      = row.getTarget(data.week)
                const pct         = actual != null && target ? actual / target : 0
                const statusColor = row.statusGood(pct) ? "#40916C" : "#C87941"
                return (
                  <div
                    key={row.label}
                    className="flex items-center gap-2 px-3.5"
                    style={{
                      paddingTop:    "11px",
                      paddingBottom: "11px",
                      borderTop: i > 0 ? "0.5px solid rgba(45,106,79,0.09)" : undefined,
                    }}
                  >
                    <div className="flex items-center gap-1.5 flex-shrink-0" style={{ width: "68px" }}>
                      <span className="w-[7px] h-[7px] rounded-full flex-shrink-0" style={{ background: statusColor }} />
                      <span className="text-[13px] font-semibold text-[#1C1C1E]">{row.label}</span>
                    </div>
                    <DotBar pct={pct} color={row.dotColor} />
                    <span
                      className="text-[12px] tabular-nums flex-shrink-0"
                      style={{ color: "#8E8E93", width: "64px", textAlign: "right" }}
                    >
                      {fmtNut(actual, target, row.unit)}
                    </span>
                  </div>
                )
              })}
            </div>
          </>
        )}

        {/* Recent orders */}
        {data.recent_orders.length > 0 && (
          <>
            <p className="text-[11px] font-bold uppercase tracking-widest px-0.5 pt-1" style={{ color: "#8E8E93" }}>
              Recent orders
            </p>
            <div
              className="bg-white rounded-xl overflow-hidden"
              style={{ border: "0.5px solid rgba(0,0,0,0.06)" }}
            >
              {data.recent_orders.map((o, i) => (
                <button
                  key={o.order_id}
                  onClick={() => router.push("/orders")}
                  className="w-full flex items-center gap-2 px-3 py-2.5 text-left"
                  style={i > 0 ? { borderTop: "0.5px solid rgba(0,0,0,0.05)" } : undefined}
                >
                  <span className="text-[11px] font-bold text-[#AEAEB2] flex-shrink-0" style={{ width: "28px" }}>
                    {fmtDay(o.placed_at)}
                  </span>
                  <span className="flex-1 text-[13px] font-medium text-[#1C1C1E] min-w-0 truncate">
                    {o.preview}
                    {o.extra_count > 0 && <span className="text-[#AEAEB2]"> +{o.extra_count}</span>}
                  </span>
                  <span className="text-[14px] font-extrabold text-[#1C1C1E] flex-shrink-0 tabular-nums">
                    {fmtINR(o.total)}
                  </span>
                  <span className="text-[12px] text-[#AEAEB2] ml-0.5">›</span>
                </button>
              ))}
            </div>
          </>
        )}

      </div>

      <BottomNav />
    </main>
  )
}
