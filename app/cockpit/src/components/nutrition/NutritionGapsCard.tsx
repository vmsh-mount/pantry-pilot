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

type Phase = "checking-flag" | "flag-off" | "loading-gaps" | "hidden" | "ready"

export function NutritionGapsCard() {
  const router = useRouter()
  const [phase, setPhase] = useState<Phase>("checking-flag")
  const [gaps, setGaps]   = useState<NutritionGap[]>([])

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

      // Flag confirmed on — show the card shell + entry rows immediately.
      // GET /v1/nutrition/gaps does live Swiggy searches + nutrition
      // resolution per recommendation and can take 15-20s+; the entry rows
      // (Settings / weekly digest / Gap-to-Cart) don't depend on this
      // request at all, so gating the whole card behind it blocked
      // navigation for the entire duration of a slow fetch — worse than
      // the silent-disappearance bug this loading state was added to fix.
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

  if (phase === "checking-flag" || phase === "flag-off" || phase === "hidden") return null

  const shortGaps = gaps.filter((g) => g.status === "short")
  const needsAttention = shortGaps.length > 0
  // compute_gaps omits on-track nutrients from the response, so an EMPTY
  // gaps array is real good news (every tracked nutrient — always
  // evaluated — is on target), not "nothing computed." Only when the array
  // is non-empty AND contains nothing but insufficient_data (no "short"
  // entries at all) is there truly no usable signal to show — that's the
  // "never really computed" case (near-zero order history in practice,
  // since core nutrients rarely fail the coverage guard once any order
  // exists). An empty array must render "on track", not hide.
  const hasNoUsableSignal = gaps.length > 0 && !needsAttention && gaps.every((g) => g.status === "insufficient_data")
  if (phase === "ready" && hasNoUsableSignal) return null

  const isLoadingGaps = phase === "loading-gaps"
  const summary = needsAttention
    ? summaryLine(shortGaps)
    : summaryLine(gaps.filter((g) => g.status === "insufficient_data"))

  return (
    <div className="bg-white rounded-xl overflow-hidden" style={{ border: "0.5px solid rgba(0,0,0,0.06)" }}>
      <div className="px-3.5 pt-3.5 pb-1 flex items-center gap-2">
        <span className="text-base">🌿</span>
        <span className="text-[13px] font-bold text-[#1C1C1E] flex-1">Nutrition</span>
        {isLoadingGaps ? (
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-full border-2 border-gray-200 border-t-[#2D6A4F] animate-spin" />
            <span className="text-[10px] text-[#8E8E93]">checking…</span>
          </span>
        ) : needsAttention ? (
          <span className="text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full bg-red-50 text-red-600">
            needs attention
          </span>
        ) : (
          <span className="text-[10px] font-semibold text-[#8E8E93]">on track</span>
        )}
      </div>

      {!isLoadingGaps && (needsAttention || summary) && (
        <p className="px-3.5 pb-2 text-[12px] text-[#5A5A5F]">{summary}</p>
      )}

      <div className="border-t" style={{ borderColor: "rgba(0,0,0,0.05)" }}>
        {[
          { label: "Household targets", sub: "per-member, in Settings", href: "/settings/targets" },
          { label: "This week's report", sub: "macros vs your targets", href: "/nutrition/weekly" },
          {
            label: "Fix these in my cart",
            sub: isLoadingGaps ? "loading…" : needsAttention ? `${shortGaps.length} item${shortGaps.length === 1 ? "" : "s"} close the gap` : "see recommendations",
            href: "/nutrition/gaps",
          },
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
