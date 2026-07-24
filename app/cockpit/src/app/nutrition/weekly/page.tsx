"use client"

/**
 * Weekly nutrition digest (Gap-to-Cart Phase B4, Screen B).
 *
 * Reuses MacroBar (NutritionCard.tsx) for calories/protein/fiber, pointed at
 * personalised_weekly_targets via GET /v1/nutrition/weekly. Sodium renders
 * via CeilingBar — never MacroBar, per the PRD's explicit visual departure.
 * No fat/carb bars: the targets PRD computes only calories/protein/fiber/
 * sodium-ceiling, so there is no fat/carb target to bar against.
 */

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { api, type NutritionWeeklyTargets, type NutritionGap } from "@/lib/api"
import { AppShell, Card, Button, Spinner } from "@/components/ui"
import { MacroBar } from "@/components/nutrition/NutritionCard"
import { CeilingBar } from "@/components/nutrition/CeilingBar"

interface WeekRow {
  week_start:        string | null
  total_calories:    number | null
  total_protein_g:   number | null
  total_fiber_g:     number | null
  total_sodium_mg:   number | null
  order_count:       number
}

const NUTRIENT_LABEL: Record<string, string> = {
  calories: "Calories", protein: "Protein", fiber: "Fiber", iron: "Iron", b12: "B12",
}

export default function WeeklyNutritionPage() {
  const router = useRouter()
  const [loading, setLoading]         = useState(true)
  const [loadFailed, setLoadFailed]   = useState(false)
  const [week, setWeek]               = useState<WeekRow | null>(null)
  const [targets, setTargets]         = useState<NutritionWeeklyTargets | null>(null)
  const [gaps, setGaps]               = useState<NutritionGap[]>([])
  const [gapsLoading, setGapsLoading] = useState(true)
  const [reloadToken, setReloadToken] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadFailed(false)
    // Decoupled from the gaps fetch below: GET /v1/nutrition/weekly is a
    // plain DB aggregation (fast). GET /v1/nutrition/gaps does live Swiggy
    // searches + resolution and can take 15-20s+. Awaiting both together
    // (the previous Promise.all) blocked this entire page — including the
    // "Fix these in my cart" CTA button, which doesn't even read gaps data
    // — behind the slow one. The macro bars now render as soon as the fast
    // call resolves; the flagged section shows its own loading row.
    ;(async () => {
      try {
        const weeklyRes = await api.nutrition.weekly(1)
        if (cancelled) return
        if (weeklyRes.success && weeklyRes.data) {
          const weeks = (weeklyRes.data as { weeks: WeekRow[] }).weeks
          setWeek(weeks[0] ?? null)
          setTargets(weeklyRes.data.weekly_targets)
        } else {
          setLoadFailed(true)
        }
      } catch {
        // No .catch() here previously meant a thrown error left `loading`
        // stuck true forever — the whole page (including the CTA button)
        // just spun with no way out.
        if (!cancelled) setLoadFailed(true)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    api.nutrition.gaps().then((gapsRes) => {
      if (cancelled) return
      setGapsLoading(false)
      if (gapsRes.success && gapsRes.data) setGaps(gapsRes.data.gaps)
    }).catch(() => { if (!cancelled) setGapsLoading(false) })

    return () => { cancelled = true }
  }, [reloadToken])

  if (loading) {
    return (
      <AppShell>
        <div className="flex items-center justify-center py-20"><Spinner size="lg" /></div>
      </AppShell>
    )
  }

  if (loadFailed) {
    return (
      <AppShell>
        <div className="flex items-center gap-3 px-1 mb-4">
          <button onClick={() => router.back()} className="text-[#D8F3DC] text-sm font-semibold">← Back</button>
          <h1 className="text-white font-bold text-lg flex-1">This week</h1>
        </div>
        <Card>
          <div className="p-6 text-sm text-gray-500 text-center space-y-3">
            <p>Couldn&apos;t load this week&apos;s nutrition.</p>
            <Button onClick={() => setReloadToken((t) => t + 1)}>Try again</Button>
          </div>
        </Card>
      </AppShell>
    )
  }

  // Mirrors the exact filter /nutrition/gaps itself uses to decide what to
  // render — not just "any short gap," but one with recommendations
  // attached. A "short" gap whose recommendation pipeline came back empty
  // would still land the user on an empty Gap-to-Cart page even with a
  // naive status-only check; matching the destination's own condition is
  // what actually makes the CTA's promise honest.
  const hasFixableGaps = gaps.some((g) => g.status === "short" && g.recommendations && g.recommendations.length > 0)

  const actualCalories = week?.total_calories ?? null
  const targetCalories = targets?.calories ?? 0

  return (
    <AppShell>
      <div className="flex items-center gap-3 px-1 mb-4">
        <button onClick={() => router.back()} className="text-[#D8F3DC] text-sm font-semibold">← Back</button>
        <h1 className="text-white font-bold text-lg flex-1">This week</h1>
      </div>

      <Card>
        <div className="px-5 pt-5">
          <div className="flex items-baseline gap-2 mb-4">
            <span className="text-3xl font-semibold tabular-nums" style={{ color: "#C45E18" }}>
              {actualCalories != null ? Math.round(actualCalories).toLocaleString("en-IN") : "—"}
            </span>
            <span className="text-sm text-gray-500">
              / {Math.round(targetCalories).toLocaleString("en-IN")} kcal target
            </span>
          </div>

          {targets && (
            <>
              <MacroBar label="Protein" value={week?.total_protein_g ?? null} target={targets.protein_g} unit="g" color="#2A60A8" />
              <MacroBar label="Fiber"   value={week?.total_fiber_g ?? null}   target={targets.fiber_g}   unit="g" color="#2A7030" />
              <CeilingBar label="Sodium" value={week?.total_sodium_mg ?? null} ceiling={targets.sodium_mg} unit="mg" />
            </>
          )}
        </div>

        {(gapsLoading || gaps.length > 0) && (
          <div className="px-5 pt-4 pb-1 mt-2 border-t border-gray-100">
            <p className="text-xs font-semibold text-gray-700 mb-2">Flagged this week</p>
            {gapsLoading && (
              <div className="flex items-center gap-2 py-1.5">
                <span className="w-3 h-3 rounded-full border-2 border-gray-200 border-t-[#2D6A4F] animate-spin shrink-0" />
                <span className="text-xs text-gray-400">Checking for gaps…</span>
              </div>
            )}
            {!gapsLoading && gaps.map((g) => {
              const label = NUTRIENT_LABEL[g.nutrient] ?? g.nutrient
              return (
                <div key={g.nutrient} className="flex items-center gap-2 py-1.5">
                  {g.status === "short" ? (
                    <>
                      <span className="text-[10px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded bg-red-50 text-red-600 shrink-0">
                        short
                      </span>
                      <span className="text-xs text-gray-700">
                        <b>{label}</b>
                        {g.short_by != null
                          ? ` — ${Math.round(g.short_by)}${g.unit ?? ""} under target`
                          : " — no source in your recent orders"}
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="text-[10px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 shrink-0">
                        data
                      </span>
                      <span className="text-xs text-gray-500">
                        <b>{label}</b> — not enough data to assess
                      </span>
                    </>
                  )}
                </div>
              )
            })}
          </div>
        )}

        <div className="px-5 pb-5 pt-3">
          {gapsLoading ? (
            // Don't offer a CTA to a page whose contents we don't know yet —
            // sending someone through a hop that might turn out empty is
            // exactly the "isn't that misleading" problem, just deferred
            // instead of avoided.
            <div className="flex items-center justify-center gap-2 py-3.5 text-xs text-gray-400">
              <span className="w-3 h-3 rounded-full border-2 border-gray-200 border-t-[#2D6A4F] animate-spin shrink-0" />
              Checking what needs fixing…
            </div>
          ) : hasFixableGaps ? (
            <Button onClick={() => router.push("/nutrition/gaps")}>Fix these in my cart →</Button>
          ) : (
            <p className="text-center text-xs text-gray-400 py-1">
              Nothing to fix this week — you&apos;re on track.
            </p>
          )}
        </div>
      </Card>
    </AppShell>
  )
}
