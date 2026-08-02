"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { api, type OrderNutrition, type NutritionConfidence, type NutritionItemBreakdown } from "@/lib/api"

// ── Confidence badge ──────────────────────────────────────────────────────────

const BADGE_CONFIG: Record<NutritionConfidence, { label: string; className: string; prefix: string }> = {
  verified: { label: "✓ Verified",  className: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200", prefix: "" },
  high:     { label: "Label",        className: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",    prefix: "" },
  medium:   { label: "~Database",    className: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300",    prefix: "~" },
  estimate: { label: "~AI est.",     className: "bg-gray-50 text-gray-400 dark:bg-gray-900 dark:text-gray-500 italic", prefix: "~" },
  unresolved: { label: "—",         className: "bg-gray-50 text-gray-400 dark:bg-gray-900 dark:text-gray-500",     prefix: "" },
  // Never actually rendered — non-food items are filtered out of the
  // per-item list before it reaches ItemRow/ConfidenceBadge (see
  // NutritionCard's item_breakdown filter below). Present only so
  // Record<NutritionConfidence, ...> stays exhaustive.
  not_food: { label: "—",           className: "bg-gray-50 text-gray-400 dark:bg-gray-900 dark:text-gray-500",     prefix: "" },
}

export function ConfidenceBadge({ confidence }: { confidence: NutritionConfidence }) {
  const cfg = BADGE_CONFIG[confidence] ?? BADGE_CONFIG.unresolved
  return (
    <span className={`inline-flex items-center text-[10px] px-1.5 py-0.5 rounded shrink-0 ${cfg.className}`}>
      {cfg.label}
    </span>
  )
}

// ── Macro bar ─────────────────────────────────────────────────────────────────

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
        <span className="text-gray-500 dark:text-gray-400">{label}</span>
        <span style={{ color }} className="tabular-nums">{display} / {targetDisplay}</span>
      </div>
      <div className="h-1 rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  )
}

// ── Per-item row ──────────────────────────────────────────────────────────────

function ItemRow({ item }: { item: NutritionItemBreakdown }) {
  const conf = item.confidence
  const kcal = item.calories != null ? `${Math.round(item.calories)} kcal` : "—"
  const isEst = conf === "estimate"
  const isUnresolved = conf === "unresolved"
  return (
    <div className="flex items-center gap-2 py-1.5 border-b border-gray-100 dark:border-gray-800 last:border-0">
      <ConfidenceBadge confidence={conf} />
      <span className="flex-1 text-xs text-gray-800 dark:text-gray-200 truncate min-w-0">{item.item_name}</span>
      <span className={`text-xs tabular-nums shrink-0 ${isUnresolved ? "text-gray-400 dark:text-gray-600" : isEst ? "text-gray-400 dark:text-gray-500 italic" : conf === "verified" ? "font-semibold text-gray-900 dark:text-gray-100" : "text-gray-600 dark:text-gray-300"}`}>
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
      // total_items counts every order item, including non-food ones (soap,
      // detergent) that resolved_items/unresolved_items both deliberately
      // exclude — see tasks/features/nutrition-non-food-gate.md. Compare
      // against the eligible total (resolved + unresolved), not the raw
      // total, so an order that's entirely non-food items (0 eligible)
      // doesn't render as an error.
      const eligibleTotal = nutrition.resolved_items + nutrition.unresolved_items
      // If nothing resolved despite having eligible items, treat as error rather than showing 0 kcal
      if (nutrition.resolved_items === 0 && eligibleTotal > 0) {
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

  // ICMR single-person weekly defaults as fallback targets (no API call needed for display)
  const targets = { calories: 14000, protein_g: 350, carbs_g: 3920, fat_g: 770, fiber_g: 175, sodium_mg: 16100 }

  // total_items counts every order item, including non-food ones that
  // resolved_items/unresolved_items both deliberately exclude — see
  // tasks/features/nutrition-non-food-gate.md. Derive both so the header
  // and footer show a denominator that actually accounts for every item.
  const eligibleTotal = data ? data.resolved_items + data.unresolved_items : 0
  const nonFoodItems = data ? data.total_items - eligibleTotal : 0

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-gray-50 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center gap-2 text-sm font-medium text-gray-800 dark:text-gray-200">
          <span>🌿</span>
          <span>Nutrition snapshot</span>
        </div>
        <span className="text-xs text-gray-400 dark:text-gray-500">
          {state === "loading" ? "computing…" : state === "error" ? "unavailable" : `${data!.resolved_items} / ${eligibleTotal} resolved`}
        </span>
      </div>

      {/* Loading */}
      {state === "loading" && (
        <div className="flex flex-col items-center gap-2 py-8 px-4">
          <div className="w-5 h-5 border-2 border-gray-200 dark:border-gray-700 border-t-green-600 rounded-full animate-spin" />
          <p className="text-sm text-gray-500 dark:text-gray-400">Resolving nutrition data</p>
          <p className="text-xs text-gray-400 dark:text-gray-500 text-center">Checking Open Food Facts, USDA &amp; AI estimates</p>
        </div>
      )}

      {/* Error */}
      {state === "error" && (
        <div className="flex flex-col items-center gap-2 py-8 px-4 text-center">
          <span className="text-2xl text-gray-300 dark:text-gray-600">⚠</span>
          <p className="text-sm text-gray-500 dark:text-gray-400">Nutrition data unavailable</p>
          <p className="text-xs text-gray-400 dark:text-gray-500">We&apos;ll retry in the background</p>
        </div>
      )}

      {/* Loaded */}
      {state === "loaded" && data && (
        <>
          <div className="px-4 pt-4 pb-2">
            {/* Calorie hero */}
            <div className="flex items-baseline gap-2 mb-4">
              <span className="text-3xl font-semibold tabular-nums" style={{ color: "#C45E18" }}>
                {data.total_calories != null ? Math.round(data.total_calories).toLocaleString() : "—"}
              </span>
              <span className="text-sm text-gray-500 dark:text-gray-400">kcal this order</span>
            </div>

            {/* Macro bars */}
            <MacroBar label="Protein"  value={data.total_protein_g}  target={targets.protein_g}  unit="g"  color="#2A60A8" />
            <MacroBar label="Carbs"    value={data.total_carbs_g}    target={targets.carbs_g}    unit="g"  color="#8A6A10" />
            <MacroBar label="Fat"      value={data.total_fat_g}      target={targets.fat_g}      unit="g"  color="#A03820" />
            <MacroBar label="Fiber"    value={data.total_fiber_g}    target={targets.fiber_g}    unit="g"  color="#2A7030" />
            <MacroBar label="Sodium"   value={data.total_sodium_mg}  target={targets.sodium_mg}  unit="mg" color="#6038A0" />

            {/* Per-item list */}
            <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-800">
              <p className="text-[10px] uppercase tracking-wider text-gray-400 dark:text-gray-500 mb-2">Per item</p>
              {/* Non-food items (soap, detergent, etc.) carry no nutrition
                  data by design — showing "no data" for them is noise, not
                  signal, so they're excluded here rather than given a row.
                  See tasks/features/nutrition-non-food-gate.md. */}
              {data.item_breakdown
                .filter((item) => item.confidence !== "not_food")
                .map((item, i) => (
                  <ItemRow key={item.sku_id ?? i} item={item} />
                ))}
            </div>
          </div>

          {/* Footer */}
          <div className="flex items-center justify-between px-4 py-2 border-t border-gray-100 dark:border-gray-800">
            <span className="text-[11px] text-gray-400 dark:text-gray-500">
              {data.resolved_items} of {eligibleTotal} items resolved
              {data.unresolved_items > 0 && ` · ${data.unresolved_items} excluded`}
              {nonFoodItems > 0 && ` · ${nonFoodItems} not food`}
            </span>
            <button className="text-[11px] text-green-700 dark:text-green-400 hover:underline">
              Report incorrect data
            </button>
          </div>
          <p className="px-4 pb-3 text-[10px] text-gray-400 dark:text-gray-500 leading-relaxed">
            Figures are estimates based on product labels and food databases. Not a substitute for medical dietary advice.
          </p>
        </>
      )}
    </div>
  )
}
