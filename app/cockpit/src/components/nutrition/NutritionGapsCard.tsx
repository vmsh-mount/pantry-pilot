"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { api, type NutritionGap } from "@/lib/api"

const NUTRIENT_LABEL: Record<string, string> = {
  calories: "Calories", protein: "Protein", fiber: "Fiber",
  iron: "Iron", b12: "B12",
}

const WATCH_NUTRIENTS = ["iron", "b12"]

function summaryLine(gaps: NutritionGap[]): string {
  const parts = gaps.slice(0, 2).map((g) => {
    const label = NUTRIENT_LABEL[g.nutrient] ?? g.nutrient
    if (g.status === "short" && g.short_by != null) {
      return `${label} ${Math.round(g.short_by)}${g.unit ?? ""} short`
    }
    return `${label} missing`
  })
  return parts.join(" · ")
}

// ── Macro rows (Calories / Protein / Fiber / Sodium) ─────────────────────────
// Always visible whenever the household has resolved nutrition data this week —
// independent of the (separately-fetched, much slower) gap-detection signal below.

export interface WeekNutrition {
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

function fmtNut(n: number | null, target: number | null, unit: string): string {
  if (n === null || target === null) return "—"
  if (unit === "mg") {
    const a = (n      / 1000).toFixed(1).replace(/\.0$/, "")
    const t = (target / 1000).toFixed(1).replace(/\.0$/, "")
    return `${a}k / ${t}k`
  }
  return `${Math.round(n)} / ${Math.round(target)}${unit}`
}

const MACRO_ROWS = [
  {
    label:      "Calories",
    dotColor:   "#FF6B00",
    getActual:  (w: WeekNutrition) => w.total_calories,
    getTarget:  (w: WeekNutrition) => w.calorie_target,
    unit:       "",
    statusGood: (pct: number) => pct >= 0.7,
  },
  {
    label:      "Protein",
    dotColor:   "#4A7FA5",
    getActual:  (w: WeekNutrition) => w.total_protein_g,
    getTarget:  (w: WeekNutrition) => w.protein_target,
    unit:       "g",
    statusGood: (pct: number) => pct >= 0.7,
  },
  {
    label:      "Fiber",
    dotColor:   "#2D6A4F",
    getActual:  (w: WeekNutrition) => w.total_fiber_g,
    getTarget:  (w: WeekNutrition) => w.fiber_target,
    unit:       "g",
    statusGood: (pct: number) => pct >= 0.7,
  },
  {
    label:      "Sodium",
    dotColor:   "#8B6336",
    getActual:  (w: WeekNutrition) => w.total_sodium_mg,
    getTarget:  (w: WeekNutrition) => w.sodium_target,
    unit:       "mg",
    statusGood: (pct: number) => pct <= 1.0,
  },
]

type Phase = "checking-flag" | "flag-off" | "loading-gaps" | "hidden" | "ready"

export function NutritionGapsCard({ week }: { week: WeekNutrition }) {
  const router = useRouter()
  const [phase, setPhase] = useState<Phase>("checking-flag")
  const [gaps, setGaps]   = useState<NutritionGap[]>([])
  const [microExpanded, setMicroExpanded] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      let flag = false
      try {
        const settingsRes = await api.settings.get()
        flag = settingsRes.success ? !!settingsRes.data?.nutrition_gaps_enabled : false
      } catch {
        flag = false
      }
      if (cancelled) return
      if (!flag) { setPhase("flag-off"); return }

      // Flag confirmed on — macro rows already render (they don't depend on
      // this call at all). GET /v1/nutrition/gaps does live Swiggy searches +
      // nutrition resolution per recommendation and can take 15-20s+; only the
      // status badge / micronutrients / nav-row subtext wait on it.
      setPhase("loading-gaps")

      try {
        const gapsRes = await api.nutrition.gaps()
        if (cancelled) return
        if (gapsRes.success && gapsRes.data) {
          setGaps(gapsRes.data.gaps)
          setPhase("ready")
        } else {
          setPhase("hidden")
        }
      } catch {
        if (!cancelled) setPhase("hidden")
      }
    })()
    return () => { cancelled = true }
  }, [])

  // Macro rows are independent of the gaps signal — if there's no resolved
  // consumption data at all this week, there's nothing to show, full stop.
  if (!week.has_nutrition_data) return null

  const shortGaps = gaps.filter((g) => g.status === "short")
  const needsAttention = shortGaps.length > 0
  // Gates the "Fix these in my cart" row specifically — distinct from
  // needsAttention (which reflects the real deficiency and should still
  // read as urgent even if, say, the search pipeline found nothing
  // purchasable for one nutrient). Mirrors the exact filter the
  // destination page itself uses, so the row is never offered as
  // clickable when it would land on an empty "No open gaps" page.
  const fixableGaps = gaps.filter((g) => g.status === "short" && g.recommendations && g.recommendations.length > 0)
  // compute_gaps omits on-track nutrients from the response, so an EMPTY
  // gaps array is real good news (every tracked nutrient — always
  // evaluated — is on target), not "nothing computed." Only when the array
  // is non-empty AND contains nothing but insufficient_data (no "short"
  // entries at all) is there truly no usable signal to show.
  const hasNoUsableSignal = gaps.length > 0 && !needsAttention && gaps.every((g) => g.status === "insufficient_data")

  // The gap-derived section (status badge, summary, micronutrients, nav rows)
  // only renders once we have a usable signal — but unlike before, failing to
  // get one no longer hides the whole card, just this section. Macro rows
  // above stay visible regardless.
  const isLoadingGaps  = phase === "loading-gaps"
  const showGapSection = isLoadingGaps || (phase === "ready" && !hasNoUsableSignal)

  // status filter matters, not just nutrient name: the watch-list backend
  // path can emit a CONFIRMED zero-intake b12/iron result (status "short",
  // watch_reason "no_source_in_window") — a more confident state than
  // "insufficient_data", already represented correctly by the summary line
  // and fixableGaps below. Without this filter it would also land here and
  // render as "no data", contradicting the summary line's "missing" on the
  // same card. See tasks/features/nutrition-gaps-coverage-disclosure.md.
  const microGaps = gaps.filter((g) => WATCH_NUTRIENTS.includes(g.nutrient) && g.status === "insufficient_data")
  const summary = needsAttention
    ? summaryLine(shortGaps)
    : summaryLine(gaps.filter((g) => g.status === "insufficient_data"))

  return (
    <div className="bg-white rounded-xl overflow-hidden" style={{ border: "0.5px solid rgba(0,0,0,0.06)" }}>

      {/* Header */}
      <div className="px-3.5 pt-3.5 pb-1 flex items-center gap-2">
        <span className="text-base">🌿</span>
        <span className="text-[13px] font-bold text-[#1C1C1E] flex-1">Nutrition</span>
        {isLoadingGaps ? (
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-full border-2 border-gray-200 border-t-[#2D6A4F] animate-spin" />
            <span className="text-[10px] text-[#8E8E93]">checking…</span>
          </span>
        ) : showGapSection ? (
          needsAttention ? (
            <span className="text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full bg-red-50 text-red-600">
              needs attention
            </span>
          ) : (
            <span className="text-[10px] font-semibold text-[#8E8E93]">on track</span>
          )
        ) : null}
      </div>

      {showGapSection && !isLoadingGaps && (needsAttention || summary) && (
        <p className="px-3.5 pb-2 text-[12px] text-[#5A5A5F]">{summary}</p>
      )}

      {/* Macro rows — Calories / Protein / Fiber / Sodium, always visible */}
      <div className="pb-1" style={{ borderTop: "0.5px solid rgba(0,0,0,0.05)" }}>
        {MACRO_ROWS.map((row, i) => {
          const actual      = row.getActual(week)
          const target      = row.getTarget(week)
          const pct         = actual != null && target ? actual / target : 0
          const statusColor = row.statusGood(pct) ? "#40916C" : "#C87941"
          return (
            <div
              key={row.label}
              className="flex items-center gap-2 px-3.5"
              style={{
                paddingTop:    "9px",
                paddingBottom: "9px",
                borderTop: i > 0 ? "0.5px solid rgba(0,0,0,0.04)" : undefined,
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

      {showGapSection && (
        <>
          {/* Micronutrients — small, dedicated reveal. No numeric target exists
              for these (watch-only), so they render as neutral chips, not bars,
              and stay collapsed by default since they're the one thing here
              that's genuinely new rather than something people already check. */}
          <button
            onClick={() => setMicroExpanded((v) => !v)}
            className="w-full flex items-center gap-1.5 px-3.5 py-2"
            style={{
              borderTop: "0.5px solid rgba(0,0,0,0.05)",
              borderBottom: "0.5px solid rgba(0,0,0,0.05)",
              background: microExpanded ? "transparent" : "#F7F8F5",
            }}
          >
            <span className="text-[11px] font-semibold text-[#8E8E93] flex-1 text-left">
              {microExpanded ? "Hide micronutrients" : isLoadingGaps ? "Micronutrients" : `Micronutrients (${microGaps.length})`}
            </span>
            <span
              className="text-[9px] text-[#8E8E93] transition-transform"
              style={{ transform: microExpanded ? "rotate(180deg)" : undefined }}
            >
              ▾
            </span>
          </button>

          {microExpanded && (
            <div className="px-3.5 py-2.5" style={{ background: "#F7F8F5", borderBottom: "0.5px solid rgba(0,0,0,0.05)" }}>
              <div className="flex flex-wrap gap-1.5 mb-2">
                {microGaps.map((g) => {
                  const label = NUTRIENT_LABEL[g.nutrient] ?? g.nutrient
                  // actual_weekly is null only when coverage is genuinely
                  // 0 (nothing resolved for this nutrient at all) — a real
                  // partial figure otherwise. Every gap here is already
                  // status === "insufficient_data" (see the microGaps
                  // filter above), so this is purely zero-vs-partial, not
                  // a confidence-state check.
                  const hasPartialSignal = g.actual_weekly != null
                  return (
                    <span
                      key={g.nutrient}
                      className="flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1 rounded-full"
                      style={{ background: "#F0F0EE", color: "#5A5A5F" }}
                    >
                      <span className="w-[5px] h-[5px] rounded-full flex-shrink-0" style={{ background: "#9CA3AF" }} />
                      {hasPartialSignal
                        ? `${label} — ~${Math.round(g.actual_weekly!)}${g.unit ?? ""} (${Math.round((g.coverage ?? 0) * 100)}% checked)`
                        : `${label} — no data`}
                    </span>
                  )
                })}
              </div>
              <p className="text-[10.5px] text-[#8E8E93] leading-relaxed">
                No numeric target for these yet — shown as watch-only until we can verify from your recent orders.
              </p>
            </div>
          )}

          {/* Navigation rows — always visible, never behind a tap */}
          <div>
            {[
              { label: "Household targets", sub: "per-member, in Settings", href: "/settings/targets" },
              { label: "This week's report", sub: "macros vs your targets", href: "/nutrition/weekly" },
              // Only offered once we actually know there's something to fix.
              ...(isLoadingGaps || fixableGaps.length > 0
                ? [{
                    label: "Fix these in my cart",
                    sub: isLoadingGaps ? "checking what needs fixing…" : `${fixableGaps.length} item${fixableGaps.length === 1 ? "" : "s"} close the gap`,
                    href: "/nutrition/gaps",
                  }]
                : []),
            ].map((row) => (
              <button
                key={row.href}
                onClick={() => router.push(row.href)}
                className="w-full flex items-center gap-2 px-3.5 py-2.5 text-left"
                style={{ borderTop: "0.5px solid rgba(0,0,0,0.05)" }}
              >
                <div className="flex-1 min-w-0">
                  <p className="text-[13px] font-semibold text-[#1C1C1E]">{row.label}</p>
                  <p className="text-[11px] text-[#8E8E93] mt-0.5">{row.sub}</p>
                </div>
                <span className="text-[12px] text-[#AEAEB2]">›</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
