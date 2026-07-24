"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { api, type NutritionGap } from "@/lib/api"

const NUTRIENT_LABEL: Record<string, string> = {
  calories: "Calories", protein: "Protein", fiber: "Fiber",
  iron: "Iron", b12: "B12",
}

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

export function NutritionGapsCard() {
  const router = useRouter()
  const [loading, setLoading] = useState(true)
  const [gaps, setGaps]       = useState<NutritionGap[] | null>(null)
  const [failed, setFailed]   = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const settingsRes = await api.settings.get()
        const flag = settingsRes.success ? settingsRes.data?.nutrition_gaps_enabled : false
        if (!flag) return

        // GET /v1/nutrition/gaps does live Swiggy searches + nutrition
        // resolution per recommendation — routinely 10-15s. Without the
        // try/catch below, any transient failure on this call (timeout,
        // dropped connection) left `loading` stuck true forever and the
        // card silently, permanently vanished for that page load with no
        // visible error — indistinguishable from "nothing to show."
        const gapsRes = await api.nutrition.gaps()
        if (cancelled) return
        if (gapsRes.success && gapsRes.data) {
          setGaps(gapsRes.data.gaps)
        } else {
          setFailed(true)
        }
      } catch {
        if (!cancelled) setFailed(true)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  if (loading) {
    // Visible "still checking" state so a slow (10-15s) fetch reads as
    // loading, not as an absent card — the exact ambiguity that made this
    // look like the card was randomly disappearing.
    return (
      <div className="bg-white rounded-xl overflow-hidden px-3.5 py-3.5 flex items-center gap-2.5"
           style={{ border: "0.5px solid rgba(0,0,0,0.06)" }}>
        <span className="w-3.5 h-3.5 rounded-full border-2 border-gray-200 border-t-[#2D6A4F] animate-spin shrink-0" />
        <span className="text-[12px] text-[#8E8E93]">Checking this week&apos;s nutrition…</span>
      </div>
    )
  }

  // A failed fetch (after retries are exhausted / on error) hides the card
  // rather than showing stale or broken data — but this is now a distinct,
  // deliberate outcome from a caught error, not an indefinite hang.
  if (failed || gaps === null) return null

  // compute_gaps omits on-track nutrients from the response entirely, so an
  // empty-of-"short" gaps array is ambiguous between two different states:
  //   (a) no order_nutrition data yet (every nutrient, including the
  //       always-evaluated calories/protein/fiber, comes back
  //       insufficient_data) — genuinely nothing to show;
  //   (b) a fully healthy household with real order history — calories/
  //       protein/fiber all on target — whose only insufficient_data entry
  //       is a micronutrient (e.g. b12/iron below the 60% coverage guard).
  // Both cases produce hasNothingToShow = true, and hiding the card is the
  // right call either way — a household with nothing to fix and no
  // coverage-worthy signal doesn't need a nutrition card on Home. But (b)
  // is not "near-zero order history"; don't debug an active household's
  // missing card by looking for missing orders — check gap statuses instead.
  const hasNothingToShow = gaps.every((g) => g.status === "insufficient_data")
  if (hasNothingToShow) return null

  const shortGaps = gaps.filter((g) => g.status === "short")
  const needsAttention = shortGaps.length > 0
  const summary = needsAttention ? summaryLine(shortGaps) : summaryLine(gaps.filter((g) => g.status === "insufficient_data"))

  return (
    <div className="bg-white rounded-xl overflow-hidden" style={{ border: "0.5px solid rgba(0,0,0,0.06)" }}>
      <div className="px-3.5 pt-3.5 pb-1 flex items-center gap-2">
        <span className="text-base">🌿</span>
        <span className="text-[13px] font-bold text-[#1C1C1E] flex-1">Nutrition</span>
        {needsAttention ? (
          <span className="text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full bg-red-50 text-red-600">
            needs attention
          </span>
        ) : (
          <span className="text-[10px] font-semibold text-[#8E8E93]">on track</span>
        )}
      </div>

      {(needsAttention || summary) && (
        <p className="px-3.5 pb-2 text-[12px] text-[#5A5A5F]">{summary}</p>
      )}

      <div className="border-t" style={{ borderColor: "rgba(0,0,0,0.05)" }}>
        {[
          { label: "Household targets", sub: "per-member, in Settings", href: "/settings/targets" },
          { label: "This week's report", sub: "macros vs your targets", href: "/nutrition/weekly" },
          { label: "Fix these in my cart", sub: needsAttention ? `${shortGaps.length} item${shortGaps.length === 1 ? "" : "s"} close the gap` : "see recommendations", href: "/nutrition/gaps" },
        ].map((row, i) => (
          <button
            key={row.href}
            onClick={() => router.push(row.href)}
            className="w-full flex items-center gap-2 px-3.5 py-2.5 text-left"
            style={i > 0 ? { borderTop: "0.5px solid rgba(0,0,0,0.05)" } : undefined}
          >
            <div className="flex-1 min-w-0">
              <p className="text-[13px] font-semibold text-[#1C1C1E]">{row.label}</p>
              <p className="text-[11px] text-[#8E8E93] mt-0.5">{row.sub}</p>
            </div>
            <span className="text-[12px] text-[#AEAEB2]">›</span>
          </button>
        ))}
      </div>
    </div>
  )
}
