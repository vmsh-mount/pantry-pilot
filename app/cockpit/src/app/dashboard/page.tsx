"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { api, type RunsListResponse } from "@/lib/api"
import { AppShell, Spinner } from "@/components/ui"

interface RoutineSummary {
  id: string
  name: string
  status: string
  next_run_at: string | null
}

export default function DashboardPage() {
  const router = useRouter()
  const [loading, setLoading]           = useState(true)
  const [runsData, setRunsData]         = useState<RunsListResponse | null>(null)
  const [routines, setRoutines]         = useState<RoutineSummary[]>([])
  const [flowInProgress, setFlowInProgress] = useState(false)
  const [nextRunAt, setNextRunAt]       = useState<string | null>(null)
  const [basketPending, setBasketPending] = useState(false)

  useEffect(() => {
    async function load() {
      setLoading(true)
      const [basketRes, runsRes, routinesRes] = await Promise.all([
        api.basket.pending(),
        api.runs.list({ limit: 1 }),
        api.routines.list(),
      ])

      if (basketRes.success && basketRes.data) {
        const d = basketRes.data as { pending?: boolean; in_progress?: boolean; next_run_at?: string }
        setBasketPending(!!d.pending)
        setFlowInProgress(!d.pending && !!d.in_progress)
        setNextRunAt(d.next_run_at ?? null)
      } else {
        const code = (basketRes.error as { code?: string })?.code
        if (code === "NOT_AUTHENTICATED" || code === "TOKEN_EXPIRED") {
          router.push("/"); return
        } else if (code === "ONBOARDING_INCOMPLETE") {
          router.push("/onboard"); return
        }
      }

      if (runsRes.success && runsRes.data) {
        setRunsData(runsRes.data as RunsListResponse)
        if (!nextRunAt) {
          const nr = (runsRes.data as RunsListResponse).next_run_at
          if (nr) setNextRunAt(nr)
        }
      }

      if (routinesRes.success && routinesRes.data) {
        setRoutines((routinesRes.data as RoutineSummary[]).filter(r => r.status === "active"))
      }

      setLoading(false)
    }
    load()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  function fmtNextRun(iso: string | null): string {
    if (!iso) return ""
    const target = new Date(iso)
    const now    = new Date()
    const tDate  = new Date(target.getFullYear(), target.getMonth(), target.getDate())
    const today  = new Date(now.getFullYear(),    now.getMonth(),    now.getDate())
    const diff   = Math.round((tDate.getTime() - today.getTime()) / 86400000)
    if (diff === 0) return "today"
    if (diff === 1) return "tomorrow"
    return target.toLocaleDateString("en-IN", { weekday: "long" })
  }

  return (
    <AppShell>
      {/* Logo bar */}
      <div className="flex items-center justify-between px-1 mb-6">
        <div className="flex items-center gap-2 text-white">
          <span className="text-xl">🥦</span>
          <span className="font-bold text-lg">PantryPilot</span>
        </div>
        <button onClick={() => router.push("/settings")} className="text-[#D8F3DC] text-sm">
          ⚙
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-24 text-white"><Spinner size="lg" /></div>
      ) : (
        <div className="space-y-4">

          {/* ── Flow card ── */}
          <button
            onClick={() => router.push("/flow")}
            className="w-full text-left bg-white rounded-2xl overflow-hidden shadow-sm"
          >
            <div className="bg-[#2D6A4F] px-5 py-4 flex items-center justify-between">
              <div>
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-white font-bold text-base">Flow</span>
                  <span className="px-1.5 py-0.5 rounded-full bg-white/20 text-[#D8F3DC] text-[10px] font-semibold tracking-wide">on</span>
                </div>
                <p className="text-[#D8F3DC] text-xs">Intelligent weekly replenishment</p>
              </div>
              <span className="text-2xl">🔄</span>
            </div>
            <div className="px-5 py-3 flex items-center justify-between">
              {basketPending ? (
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-[#2D6A4F] shrink-0" />
                  <span className="text-sm font-semibold text-[#2D6A4F]">Basket ready for review</span>
                </div>
              ) : flowInProgress ? (
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse shrink-0" />
                  <span className="text-sm font-medium text-gray-700">Building your basket…</span>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-gray-300 shrink-0" />
                  <span className="text-sm text-gray-500">
                    {nextRunAt ? `Next run ${fmtNextRun(nextRunAt)}` : "No run scheduled"}
                  </span>
                </div>
              )}
              <span className="text-gray-300 text-sm">›</span>
            </div>
          </button>

          {/* ── Routines card ── */}
          <button
            onClick={() => router.push("/routines")}
            className="w-full text-left bg-white rounded-2xl overflow-hidden shadow-sm"
          >
            <div className="bg-[#2D6A4F] px-5 py-4 flex items-center justify-between">
              <div>
                <span className="text-white font-bold text-base">Routines</span>
                <p className="text-[#D8F3DC] text-xs mt-0.5">Scheduled recurring orders</p>
              </div>
              <span className="text-2xl">📋</span>
            </div>
            <div className="px-5 py-3 flex items-center justify-between">
              {routines.length === 0 ? (
                <span className="text-sm text-gray-400">No active routines</span>
              ) : (
                <span className="text-sm text-gray-700">
                  <span className="font-semibold text-gray-900">{routines.length}</span> active
                  {routines[0]?.next_run_at && (
                    <span className="text-gray-400"> · next {new Date(routines[0].next_run_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</span>
                  )}
                </span>
              )}
              <span className="text-gray-300 text-sm">›</span>
            </div>
          </button>

          {/* ── Quick Order card ── */}
          <button
            onClick={() => router.push("/quick")}
            className="w-full text-left bg-white rounded-2xl overflow-hidden shadow-sm"
          >
            <div className="bg-[#2D6A4F] px-5 py-4 flex items-center justify-between">
              <div>
                <span className="text-white font-bold text-base">Quick Order</span>
                <p className="text-[#D8F3DC] text-xs mt-0.5">Order anything from Swiggy now</p>
              </div>
              <span className="text-2xl">🛒</span>
            </div>
            <div className="px-5 py-3 flex items-center justify-between">
              <span className="text-sm text-gray-500">Search and order in minutes</span>
              <span className="text-gray-300 text-sm">›</span>
            </div>
          </button>

          {/* ── Quick stats ── */}
          {(runsData?.stats?.total_runs ?? 0) > 0 && (
            <div className="grid grid-cols-3 gap-2 pt-1">
              {[
                { label: "Total runs",   value: String(runsData!.stats.total_runs) },
                { label: "Last order",   value: runsData!.stats.last_order_total != null ? `₹${Math.round(runsData!.stats.last_order_total).toLocaleString("en-IN")}` : "—" },
                { label: "Weekly avg",   value: runsData!.stats.avg_order_total  != null ? `₹${Math.round(runsData!.stats.avg_order_total ).toLocaleString("en-IN")}` : "—" },
              ].map(s => (
                <div key={s.label} className="bg-white/10 rounded-2xl px-3 py-3 text-center">
                  <p className="text-white font-semibold text-sm">{s.value}</p>
                  <p className="text-[#D8F3DC] text-[10px] mt-0.5">{s.label}</p>
                </div>
              ))}
            </div>
          )}

        </div>
      )}
    </AppShell>
  )
}
