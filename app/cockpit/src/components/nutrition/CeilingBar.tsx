"use client"

/**
 * Sodium's distinct ceiling treatment (Gap-to-Cart Phase B4).
 *
 * Deliberately NOT a MacroBar. A macro bar reads as "progress toward a
 * goal" — a near-full bar looks like an achievement. Sodium is a ceiling
 * (upper limit): a near-full bar means "approaching the limit," a red
 * bar means "over," and the label is a status word, never a bare percentage
 * ("92% of a ceiling" reads as almost-there when it should read as caution).
 */

export function CeilingBar({
  label,
  value,
  ceiling,
  unit,
}: {
  label: string
  value: number | null
  ceiling: number
  unit: string
}) {
  const pct = value != null ? Math.min(100, Math.round((value / ceiling) * 100)) : 0
  const status: "under" | "approaching" | "over" =
    value == null ? "under" : pct >= 100 ? "over" : pct >= 85 ? "approaching" : "under"

  const statusConfig = {
    under:       { label: "under limit ✓",     color: "#2A7030", track: "#DCFCE7" },
    approaching: { label: "approaching limit",  color: "#B45309", track: "#FEF3C7" },
    over:        { label: "over limit",         color: "#A03820", track: "#FBE6E0" },
  }[status]

  const display = value != null ? `${Math.round(value).toLocaleString("en-IN")}${unit}` : "—"
  const ceilingDisplay = `${Math.round(ceiling).toLocaleString("en-IN")}${unit}`

  return (
    <div className="mt-3 pt-3 border-t border-dashed border-gray-200 dark:border-gray-700">
      <div className="flex items-center justify-between mb-1">
        <span className="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
          {label}
          <span className="text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300">
            ceiling
          </span>
        </span>
        <span className="text-xs font-semibold" style={{ color: statusConfig.color }}>
          {statusConfig.label}
        </span>
      </div>
      <div className="relative h-1 rounded-full overflow-hidden" style={{ background: statusConfig.track }}>
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${pct}%`, background: statusConfig.color }}
        />
        {/* hard limit marker at the right edge */}
        <span
          className="absolute top-1/2 -translate-y-1/2 w-0.5 h-2 rounded-sm bg-red-500"
          style={{ right: 0 }}
        />
      </div>
      <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-1">
        {display} / {ceilingDisplay} — headroom to the cap, not a goal to reach
      </p>
    </div>
  )
}
