"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { api, type OrderNutrition, type NutritionConfidence, type NutritionItemBreakdown } from "@/lib/api"

// ── Design tokens (matches components/ui.tsx) ─────────────────────────────────

const T = {
  green:  "#2D6A4F",
  ink:    "#1C1C1E",
  ink2:   "#5A5A5F",
  ink3:   "#8E8E93",
  hair:   "rgba(0,0,0,0.07)",
  bg:     "#F7F8F5",
}

// Composition-bar macro colors — also used by the confidence-icon "database
// match" tiers below for a consistent gray, kept separate from macro colors.
const MACRO_COLOR = { protein: "#4A7FA5", carbs: "#D9A24E", fat: "#C0665C" }

// ── Confidence badge (kept — reused by nutrition/gaps/page.tsx) ──────────────

const BADGE_CONFIG: Record<NutritionConfidence, { label: string; className: string; prefix: string }> = {
  verified: { label: "✓ Verified",  className: "bg-green-100 text-green-800", prefix: "" },
  high:     { label: "Label",        className: "bg-blue-100 text-blue-800",   prefix: "" },
  medium:   { label: "~Database",    className: "bg-gray-100 text-gray-600",   prefix: "~" },
  estimate: { label: "~AI est.",     className: "bg-gray-50 text-gray-400 italic", prefix: "~" },
  unresolved: { label: "—",         className: "bg-gray-50 text-gray-400",    prefix: "" },
}

export function ConfidenceBadge({ confidence }: { confidence: NutritionConfidence }) {
  const cfg = BADGE_CONFIG[confidence] ?? BADGE_CONFIG.unresolved
  return (
    <span className={`inline-flex items-center text-[10px] px-1.5 py-0.5 rounded shrink-0 ${cfg.className}`}>
      {cfg.label}
    </span>
  )
}

// ── Macro bar (kept — reused by nutrition/weekly/page.tsx for weekly actual
//    vs. weekly target, which is the one place that comparison is correct) ──

export function MacroBar({
  label,
  value,
  target,
  unit,
  color,
}: {
  label: string
  value: number | null
  target: number
  unit: string
  color: string
}) {
  const pct = value != null ? Math.min(100, Math.round((value / target) * 100)) : 0
  const display = value != null ? `${Math.round(value)}${unit}` : "—"
  const targetDisplay = `${Math.round(target)}${unit}`
  return (
    <div className="mb-2">
      <div className="flex justify-between text-xs mb-1">
        <span className="text-gray-500">{label}</span>
        <span style={{ color }} className="tabular-nums">{display} / {targetDisplay}</span>
      </div>
      <div className="h-1 rounded-full bg-gray-100 overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  )
}

// ── Composition bar — replaces per-order Protein/Carbs/Fat MacroBar rows ─────
// Shows what THIS order's calories are made of (Atwater factors), normalized
// against the sum of the three macro-derived calories — not total_calories —
// so the bar always sums to exactly 100% regardless of any rounding drift
// between how total_calories and the per-macro grams were independently
// resolved. No external target: composition is coherent for one order in
// isolation, unlike "progress toward a weekly target."

function CompositionBar({
  proteinG,
  carbsG,
  fatG,
}: {
  proteinG: number | null
  carbsG:   number | null
  fatG:     number | null
}) {
  const proteinKcal = (proteinG ?? 0) * 4
  const carbsKcal   = (carbsG   ?? 0) * 4
  const fatKcal     = (fatG     ?? 0) * 9
  const sumKcal     = proteinKcal + carbsKcal + fatKcal

  if (sumKcal <= 0) return null

  const segments = [
    { label: "Protein", grams: proteinG, kcal: proteinKcal, color: MACRO_COLOR.protein },
    { label: "Carbs",   grams: carbsG,   kcal: carbsKcal,   color: MACRO_COLOR.carbs },
    { label: "Fat",     grams: fatG,     kcal: fatKcal,     color: MACRO_COLOR.fat },
  ]

  return (
    <>
      <div className="flex overflow-hidden rounded-md mb-2.5" style={{ height: 10 }}>
        {segments.map((s) => (
          <div key={s.label} style={{ width: `${(s.kcal / sumKcal) * 100}%`, background: s.color }} />
        ))}
      </div>
      <div className="flex gap-3.5 mb-3.5">
        {segments.map((s) => (
          <div key={s.label} className="flex items-start gap-1.5">
            <span className="w-2 h-2 rounded-full mt-0.5 shrink-0" style={{ background: s.color }} />
            <div>
              <p className="text-[11px] font-semibold" style={{ color: T.ink }}>{s.label}</p>
              <p className="text-[10.5px] tabular-nums" style={{ color: T.ink3 }}>
                {Math.round(s.grams ?? 0)}g · {Math.round((s.kcal / sumKcal) * 100)}%
              </p>
            </div>
          </div>
        ))}
      </div>
    </>
  )
}

// ── Fiber / Sodium — plain stat tiles, not bars ───────────────────────────────
// Neither has a coherent "share of this order's calories" reading, and a
// weekly ceiling reintroduces the exact wrong-denominator problem this
// redesign exists to fix. Absolute numbers are the honest choice.

function StatTile({ value, unit, label }: { value: number | null; unit: string; label: string }) {
  return (
    <div className="flex-1 rounded-xl px-3 py-2.5" style={{ background: T.bg }}>
      <p className="text-[16px] font-extrabold tabular-nums" style={{ color: T.ink }}>
        {value != null ? `${Math.round(value * 10) / 10}${unit}` : "—"}
      </p>
      <p className="text-[10px] font-semibold uppercase tracking-wide mt-0.5" style={{ color: T.ink3 }}>{label}</p>
    </div>
  )
}

// ── Confidence icon — replaces the boxed badge in this component's own item
//    list. 5 states (verified > high > medium > estimate > unresolved), each
//    a distinct glyph — not just a color — with the full label on `title`
//    (native hover tooltip; degrades to long-press on most mobile browsers).

const CONFIDENCE_ICON: Record<NutritionConfidence, { title: string; color: string; path: React.ReactNode }> = {
  verified: {
    title: "Verified — confirmed accurate",
    color: T.green,
    path: <path d="M20 6 9 17l-5-5" />,
  },
  high: {
    title: "High confidence — matched from Open Food Facts",
    color: "#4A7FA5",
    path: <><circle cx="12" cy="12" r="9" /><path d="m8.5 12 2.5 2.5 4.5-4.5" /></>,
  },
  medium: {
    title: "Database match — from a food database",
    color: T.ink3,
    path: <><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4a2 2 0 0 0 1-1.73z" /><path d="M3.3 7 12 12l8.7-5" /><path d="M12 22V12" /></>,
  },
  estimate: {
    title: "AI estimate — no direct match found",
    color: "#D9A24E",
    path: <path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z" />,
  },
  unresolved: {
    title: "Unresolved — no nutrition data available",
    color: "#D0D0D0",
    path: <path d="M5 12h14" />,
  },
}

function ConfidenceIcon({ confidence }: { confidence: NutritionConfidence }) {
  const cfg = CONFIDENCE_ICON[confidence] ?? CONFIDENCE_ICON.unresolved
  return (
    <span title={cfg.title} className="inline-flex items-center justify-center shrink-0" style={{ width: 14, height: 14, cursor: "help" }}>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={cfg.color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
        {cfg.path}
      </svg>
    </span>
  )
}

// ── Per-item row ──────────────────────────────────────────────────────────────

function ItemRow({ item }: { item: NutritionItemBreakdown }) {
  const conf = item.confidence
  const kcal = item.calories != null ? `${Math.round(item.calories)} kcal` : "—"
  const isEst = conf === "estimate"
  const isUnresolved = conf === "unresolved"
  return (
    <div className="flex items-center gap-2 py-[7px]" style={{ borderTop: `0.5px solid ${T.hair}` }}>
      <ConfidenceIcon confidence={conf} />
      <span
        className="flex-1 text-xs truncate min-w-0"
        style={{ color: isUnresolved ? T.ink3 : T.ink, fontStyle: isEst ? "italic" : undefined }}
      >
        {item.item_name}
      </span>
      <span
        className="text-xs tabular-nums shrink-0"
        style={{
          color: isUnresolved ? "#C7C7CC" : isEst ? T.ink3 : conf === "verified" ? T.ink : T.ink2,
          fontWeight: conf === "verified" ? 600 : 400,
          fontStyle: isEst ? "italic" : undefined,
        }}
      >
        {isEst && item.calories != null ? `~${Math.round(item.calories)} kcal` : kcal}
      </span>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  orderId: string
}

export function NutritionCard({ orderId }: Props) {
  const [state, setState] = useState<"loading" | "loaded" | "error">("loading")
  const [data, setData] = useState<OrderNutrition | null>(null)
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)
  const retryRef = useRef(0)

  const poll = useCallback(async () => {
    try {
      const apiRes = await api.nutrition.order(orderId)
      if (!mountedRef.current) return
      const payload = apiRes.data as OrderNutrition | { status: "computing"; retry_after: number } | undefined
      if (!payload || ("status" in payload && payload.status === "computing")) {
        if (retryRef.current >= 5) { setState("error"); return }
        retryRef.current += 1
        pollRef.current = setTimeout(poll, 10_000)
        return
      }
      const nutrition = payload as OrderNutrition
      // If nothing resolved, treat as error rather than showing 0 kcal
      if (nutrition.resolved_items === 0 && nutrition.total_items > 0) {
        setState("error")
        return
      }
      setData(nutrition)
      setState("loaded")
    } catch {
      if (mountedRef.current) setState("error")
    }
  }, [orderId])

  useEffect(() => {
    mountedRef.current = true
    poll()
    return () => {
      mountedRef.current = false
      if (pollRef.current) clearTimeout(pollRef.current)
    }
  }, [poll])

  return (
    <div className="rounded-xl overflow-hidden" style={{ border: `1px solid ${T.hair}`, background: "#fff" }}>
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3">
        <span style={{ fontSize: 15 }}>🌿</span>
        <span className="text-[13px] font-bold flex-1" style={{ color: T.ink }}>Nutrition snapshot</span>
        <span className="text-[10.5px] font-semibold" style={{ color: T.ink3 }}>
          {state === "loading" ? "computing…" : state === "error" ? "unavailable" : `${data!.resolved_items}/${data!.total_items} resolved`}
        </span>
      </div>

      {/* Loading */}
      {state === "loading" && (
        <div className="flex flex-col items-center gap-2 py-8 px-4">
          <div className="w-5 h-5 rounded-full animate-spin" style={{ border: "2px solid #E5E7EB", borderTopColor: T.green }} />
          <p className="text-sm" style={{ color: T.ink3 }}>Resolving nutrition data</p>
          <p className="text-xs text-center" style={{ color: T.ink3 }}>Checking Open Food Facts, USDA &amp; AI estimates</p>
        </div>
      )}

      {/* Error */}
      {state === "error" && (
        <div className="flex flex-col items-center gap-2 py-8 px-4 text-center">
          <span className="text-2xl" style={{ color: "#D0D0D0" }}>⚠</span>
          <p className="text-sm" style={{ color: T.ink3 }}>Nutrition data unavailable</p>
          <p className="text-xs" style={{ color: T.ink3 }}>We&apos;ll retry in the background</p>
        </div>
      )}

      {/* Loaded */}
      {state === "loaded" && data && (
        <>
          <div className="px-4 pb-1">
            {/* Calorie hero */}
            <div className="flex items-baseline gap-1.5 mb-3">
              <span className="text-[32px] font-extrabold tabular-nums" style={{ letterSpacing: "-0.5px", color: T.ink }}>
                {data.total_calories != null ? Math.round(data.total_calories).toLocaleString() : "—"}
              </span>
              <span className="text-[13px]" style={{ color: T.ink3 }}>kcal this order</span>
            </div>

            {/* Composition */}
            <CompositionBar proteinG={data.total_protein_g} carbsG={data.total_carbs_g} fatG={data.total_fat_g} />

            {/* Fiber / Sodium */}
            <div className="flex gap-2.5 mb-3.5">
              <StatTile value={data.total_fiber_g} unit="g" label="Fiber" />
              <StatTile value={data.total_sodium_mg != null ? data.total_sodium_mg / 1000 : null} unit="g" label="Sodium" />
            </div>

            {/* Per-item list */}
            <div className="pt-3" style={{ borderTop: `1px solid ${T.hair}` }}>
              <p className="text-[9.5px] font-extrabold uppercase tracking-wider mb-1.5" style={{ color: T.ink3 }}>Per item</p>
              {data.item_breakdown.map((item, i) => (
                <ItemRow key={item.sku_id ?? i} item={item} />
              ))}
            </div>
          </div>

          {/* Footer */}
          <div className="flex items-center justify-between px-4 py-2.5" style={{ borderTop: `1px solid ${T.hair}` }}>
            <span className="text-[10.5px]" style={{ color: T.ink3 }}>
              {data.resolved_items} of {data.total_items} items resolved
              {data.unresolved_items > 0 && ` · ${data.unresolved_items} excluded`}
            </span>
            <button className="text-[10.5px] font-semibold" style={{ color: T.green }}>
              Report incorrect data
            </button>
          </div>
          <p className="px-4 pb-3 text-[10px] leading-relaxed" style={{ color: T.ink3 }}>
            Figures are estimates based on product labels and food databases. Not a substitute for medical dietary advice.
          </p>
        </>
      )}
    </div>
  )
}
