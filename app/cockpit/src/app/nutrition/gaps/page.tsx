"use client"

/**
 * Gap-to-Cart recommendations (Gap-to-Cart Phase B4, Screen C) — the payoff
 * screen: "it doesn't just name the gap, it puts the fix in the cart."
 *
 * Add-to-cart routing (the part that broke in review — see PRD):
 *   1. An awaiting_confirmation LoopRun exists -> POST /v1/basket/add-item
 *      (adds to the pending Flow basket).
 *   2. Otherwise (the common case) -> Quick Order add.
 *   3. Never silently triggers a new Flow planning run.
 * Button copy is "Add to cart" — never "Add to Flow basket" — because the
 * routing can't guarantee which basket it lands in.
 */

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { api, type NutritionGap, type GapRecommendation } from "@/lib/api"
import { AppShell, Card, Button, Spinner } from "@/components/ui"
import { ConfidenceBadge } from "@/components/nutrition/NutritionCard"

const NUTRIENT_LABEL: Record<string, string> = {
  calories: "Calories", protein: "Protein", fiber: "Fiber", iron: "Iron", b12: "B12",
}

type AddState = "idle" | "adding" | "added" | "error"

function RecommendationCard({
  rec, unit, isTop, addState, onAdd,
}: {
  rec: GapRecommendation
  unit: string
  isTop: boolean
  addState: AddState
  onAdd: () => void
}) {
  return (
    <div className="border border-gray-100 rounded-2xl p-3 flex items-center gap-3 mb-2 last:mb-0">
      <div className="w-10 h-10 rounded-xl bg-[#D8F3DC] flex items-center justify-center text-lg shrink-0">🛒</div>
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-semibold text-gray-900 truncate">{rec.item_name}</p>
        <div className="flex items-center gap-1.5 mt-0.5">
          {rec.brand && <span className="text-[10px] text-gray-400 truncate">{rec.brand}</span>}
          <ConfidenceBadge confidence={rec.confidence} />
        </div>
        <p className="text-[11px] font-semibold mt-1" style={{ color: "#2A60A8" }}>
          +{Math.round(rec.delivers)}{unit}{isTop ? " · best per ₹" : ""}
        </p>
        {rec.repurchase_rate != null && rec.repurchase_rate > 0 && (
          <p className="text-[10px] text-gray-400 mt-0.5">
            reordered by {Math.round(rec.repurchase_rate * 100)}% of buyers
          </p>
        )}
      </div>
      <div className="flex flex-col items-end gap-1.5 shrink-0">
        <span className="text-[13px] font-bold text-gray-900 tabular-nums">₹{Math.round(rec.unit_price)}</span>
        <button
          onClick={onAdd}
          disabled={addState === "adding" || addState === "added"}
          className={`text-[11px] font-bold px-3 py-1.5 rounded-full whitespace-nowrap transition-colors ${
            addState === "added"
              ? "bg-green-50 text-green-700"
              : "bg-[#2D6A4F] text-white hover:bg-[#1B4332]"
          }`}
        >
          {addState === "adding" ? "…" : addState === "added" ? "✓ Added" : addState === "error" ? "Retry" : "+ Add"}
        </button>
      </div>
    </div>
  )
}

export default function GapsToCartPage() {
  const router = useRouter()
  const [loading, setLoading]           = useState(true)
  const [gaps, setGaps]                 = useState<NutritionGap[]>([])
  const [basketPending, setBasketPending] = useState(false)
  const [addStates, setAddStates]       = useState<Record<string, AddState>>({})

  useEffect(() => {
    Promise.all([api.nutrition.gaps(), api.dashboard.get()]).then(([gapsRes, dashRes]) => {
      setLoading(false)
      if (gapsRes.success && gapsRes.data) setGaps(gapsRes.data.gaps)
      const dash = dashRes.data as { flow?: { basket_pending?: boolean } } | undefined
      setBasketPending(!!dash?.flow?.basket_pending)
    })
  }, [])

  async function addToCart(rec: GapRecommendation) {
    setAddStates((s) => ({ ...s, [rec.sku_id]: "adding" }))
    try {
      const res = basketPending
        ? await api.basket.addItem({
            sku_id: rec.sku_id, item_name: rec.item_name,
            unit_price: rec.unit_price, brand: rec.brand ?? undefined,
          })
        : await api.quick.addItem({
            sku_id: rec.sku_id, item_name: rec.item_name,
            unit_price: rec.unit_price, brand: rec.brand ?? undefined,
            in_stock: rec.in_stock,
          })
      setAddStates((s) => ({ ...s, [rec.sku_id]: res.success ? "added" : "error" }))
    } catch {
      setAddStates((s) => ({ ...s, [rec.sku_id]: "error" }))
    }
  }

  async function addAllTopPicks() {
    const topPicks = shortGaps
      .map((g) => g.recommendations?.[0])
      .filter((r): r is GapRecommendation => !!r)
    for (const rec of topPicks) {
      if (addStates[rec.sku_id] !== "added") await addToCart(rec)
    }
  }

  if (loading) {
    return (
      <AppShell>
        <div className="flex items-center justify-center py-20"><Spinner size="lg" /></div>
      </AppShell>
    )
  }

  const shortGaps = gaps.filter((g) => g.status === "short" && g.recommendations && g.recommendations.length > 0)

  return (
    <AppShell>
      <div className="flex items-center gap-3 px-1 mb-4">
        <button onClick={() => router.back()} className="text-[#D8F3DC] text-sm font-semibold">← Back</button>
        <h1 className="text-white font-bold text-lg flex-1">Close your gaps</h1>
      </div>

      {shortGaps.length === 0 ? (
        <Card>
          <div className="p-6 text-center">
            <p className="text-sm text-gray-500">No open gaps with recommendations right now.</p>
          </div>
        </Card>
      ) : (
        <>
          {shortGaps.map((gap) => {
            const label = NUTRIENT_LABEL[gap.nutrient] ?? gap.nutrient
            const unit = gap.unit ?? "g"
            return (
              <Card key={gap.nutrient} className="mb-3">
                <div className="px-4 pt-4">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[10px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded bg-red-50 text-red-600">
                      {label}
                      {gap.short_by != null ? ` · ${Math.round(gap.short_by)}${unit} short` : ""}
                    </span>
                  </div>
                  <p className="text-[11px] text-gray-400 mb-3">
                    Ranked by {label.toLowerCase()} per ₹ · diet-safe · in stock
                  </p>
                </div>
                <div className="px-4 pb-4">
                  {gap.recommendations!.map((rec, i) => (
                    <RecommendationCard
                      key={rec.sku_id}
                      rec={rec}
                      unit={unit}
                      isTop={i === 0}
                      addState={addStates[rec.sku_id] ?? "idle"}
                      onAdd={() => addToCart(rec)}
                    />
                  ))}
                </div>
              </Card>
            )
          })}

          <div className="px-1 pb-2">
            <Button onClick={addAllTopPicks}>Add all to cart</Button>
            <p className="text-center text-[11px] text-white/60 mt-2">
              → your open Flow basket if one&apos;s pending, else a Quick Order
            </p>
          </div>
        </>
      )}

      <p className="px-2 pb-4 text-[10px] text-white/40 leading-relaxed text-center">
        Estimates from labels &amp; food databases. Not medical advice. A single
        pack can numerically close a weekly gap — that&apos;s a week&apos;s
        worth, not one meal&apos;s fix.
      </p>
    </AppShell>
  )
}
