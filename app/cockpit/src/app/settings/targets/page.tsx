"use client"

/**
 * Household targets — per-member breakdown (Gap-to-Cart Phase B4, Screen A).
 * Reads GET /v1/nutrition/targets (Phase A). Computes nothing itself —
 * purely a rendering of what the backend already derived from
 * personalised_weekly_targets / per_member_targets.
 */

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { api, type NutritionTargetsResponse } from "@/lib/api"
import { AppShell, Card, Spinner } from "@/components/ui"

const ROLE_ICON: Record<string, string> = {
  adult: "🧑", elderly: "🧓", child: "🧒", infant: "👶",
}

const HEALTH_FLAG_NOTE: Record<string, string> = {
  hypertension: "lower sodium ceiling applied",
  kidney_disease: "adjusted sodium ceiling applied",
}

export default function HouseholdTargetsPage() {
  const router = useRouter()
  const [loading, setLoading] = useState(true)
  const [data, setData]       = useState<NutritionTargetsResponse | null>(null)

  useEffect(() => {
    api.nutrition.targets().then((res) => {
      setLoading(false)
      if (res.success && res.data) setData(res.data)
    })
  }, [])

  if (loading) {
    return (
      <AppShell>
        <div className="flex items-center justify-center py-20"><Spinner size="lg" /></div>
      </AppShell>
    )
  }

  if (!data) {
    return (
      <AppShell>
        <div className="flex items-center gap-3 px-1 mb-4">
          <button onClick={() => router.back()} className="text-[#D8F3DC] text-sm font-semibold">← Back</button>
          <h1 className="text-white font-bold text-lg flex-1">Household targets</h1>
        </div>
        <Card><div className="p-6 text-sm text-gray-500">Couldn&apos;t load targets.</div></Card>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <div className="flex items-center gap-3 px-1 mb-4">
        <button onClick={() => router.back()} className="text-[#D8F3DC] text-sm font-semibold">← Back</button>
        <h1 className="text-white font-bold text-lg flex-1">Household targets</h1>
      </div>

      <Card>
        <div className="px-5 pt-5 pb-1">
          <h2 className="text-base font-bold text-gray-900">
            {data.source === "personalized" ? "Personalized" : "Estimated"} · {data.per_member.length} member{data.per_member.length === 1 ? "" : "s"}
          </h2>
          <p className="text-xs text-gray-400 mt-0.5">
            {data.source === "personalized"
              ? "Mifflin-St Jeor + activity · protein by body weight"
              : "Some members are missing biometrics — using role-based estimates"}
          </p>
        </div>

        <div className="px-5 pt-2">
          {data.per_member.map((m, i) => (
            <div key={m.member_id} className={`flex items-center gap-3 py-3 ${i > 0 ? "border-t border-gray-100" : ""}`}>
              <div className="w-9 h-9 rounded-xl bg-[#D8F3DC] text-[#1B4332] flex items-center justify-center text-base shrink-0">
                {ROLE_ICON[m.role ?? "adult"] ?? "🧑"}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <p className="text-sm font-semibold text-gray-900 capitalize">{m.role ?? "Member"}</p>
                  {m.fallback_used && (
                    <span className="text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">
                      estimated
                    </span>
                  )}
                </div>
                <p className="text-[11px] text-gray-400">
                  {m.age_years != null ? `${m.age_years} yrs` : "age unknown"}
                </p>
                {m.health_flags.length > 0 && (
                  <p className="text-[11px] text-amber-600 mt-0.5">
                    {m.health_flags.map((f) => HEALTH_FLAG_NOTE[f] ?? f).join(" · ")}
                  </p>
                )}
              </div>
              <div className="text-right shrink-0 tabular-nums">
                <p className="text-sm font-bold text-gray-900">{Math.round(m.daily.calories)}</p>
                <p className="text-[10px] text-gray-400">{Math.round(m.daily.protein_g)}g protein</p>
              </div>
            </div>
          ))}
        </div>

        <div className="mx-5 mt-2 pt-3 border-t-2 border-gray-100 flex justify-between items-center">
          <span className="text-sm font-bold text-gray-900">Household / day</span>
          <span className="text-lg font-bold tabular-nums" style={{ color: "#C45E18" }}>
            {Math.round(data.household.daily.calories).toLocaleString("en-IN")}
          </span>
        </div>
        <div className="mx-5 pb-5 flex justify-between items-center text-sm">
          <span className="text-gray-500">Protein / day</span>
          <span className="font-semibold text-gray-800 tabular-nums">{Math.round(data.household.daily.protein_g)}g</span>
        </div>
      </Card>
    </AppShell>
  )
}
