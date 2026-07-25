"use client"

import { useEffect, useState } from "react"
import { useRouter, useParams } from "next/navigation"
import { api } from "@/lib/api"
import { PageShell, PageHero, BackButton, Card, Button, Alert, Spinner } from "@/components/ui"
import { NutritionCard } from "@/components/nutrition/NutritionCard"

interface RoutineItem  { id: string; item_name: string; quantity: number; unit: string }
interface RoutineRun   { id: string; scheduled_at: string; status: string; skip_reason?: string; total_amount?: number; order_id?: string }
interface Routine {
  id: string; name: string; status: string
  frequency_type: string; frequency_value: number
  schedule_time_ist: string; next_run_at: string | null
  end_date: string | null; runs_remaining: number | null
  items: RoutineItem[]; upcoming_runs: string[]
}

function freqLabel(r: Routine): string {
  if (r.frequency_type === "every_n_days") {
    if (r.frequency_value === 1) return "Every day"
    if (r.frequency_value === 7) return "Every week"
    if (r.frequency_value === 14) return "Every 2 weeks"
    return `Every ${r.frequency_value} days`
  }
  if (r.frequency_type === "weekly") {
    const days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    return `Every ${days[r.frequency_value] ?? ""}`
  }
  if (r.frequency_type === "monthly") return `Monthly, day ${r.frequency_value}`
  return ""
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" })
}
function fmtDateTime(iso: string) {
  return new Date(iso).toLocaleDateString("en-IN", {
    weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit",
  })
}

export default function RoutineDetailPage() {
  const router   = useRouter()
  const { id }   = useParams<{ id: string }>()

  const [routine, setRoutine]       = useState<Routine | null>(null)
  const [runs, setRuns]             = useState<RoutineRun[]>([])
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState("")
  const [actionLoading, setAction]  = useState<string | null>(null)
  const [dialog, setDialog]         = useState<"delete" | "pause" | null>(null)
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.routines.get(id), api.routines.runs(id)]).then(([r, runs]) => {
      setLoading(false)
      if (r.success && r.data) setRoutine(r.data as Routine)
      else if (r.error?.code === "NOT_AUTHENTICATED") router.replace("/")
      else setError("Routine not found.")
      if (runs.success && runs.data) setRuns(runs.data as RoutineRun[])
    })
  }, [id, router])

  async function handlePause() {
    setAction("pause")
    const res = await api.routines.pause(id)
    if (res.success && res.data) setRoutine(res.data as Routine)
    setAction(null); setDialog(null)
  }

  async function handleResume() {
    setAction("resume")
    const res = await api.routines.resume(id)
    if (res.success && res.data) setRoutine(res.data as Routine)
    setAction(null)
  }

  async function handleSkipNext() {
    setAction("skip")
    const res = await api.routines.skipNext(id)
    if (res.success && res.data) setRoutine(res.data as Routine)
    setAction(null)
  }

  async function handleDelete() {
    setAction("delete")
    await api.routines.delete(id)
    router.push("/routines")
  }

  if (loading) return (
    <PageShell hero={<PageHero title="Routine details" back={() => router.push("/routines")} />}>
      <div className="flex justify-center py-20" style={{ color: "#8E8E93" }}><Spinner size="lg" /></div>
    </PageShell>
  )

  if (!routine) return (
    <PageShell hero={<PageHero title="Routine details" back={() => router.push("/routines")} />}>
      <Card><div className="px-6 py-8"><Alert type="error" message={error || "Not found."} /></div></Card>
    </PageShell>
  )

  const isActive = routine.status === "active"
  const isPaused = routine.status === "paused"

  const STATUS_CHIP: Record<string, string> = {
    active: "bg-[#D8F3DC] text-[#1B4332]",
    paused: "bg-amber-100 text-amber-800",
    ended:  "bg-gray-100 text-gray-500",
  }

  const hero = (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-3">
        <BackButton variant="on-green" onClick={() => router.push("/routines")} />
        <h2 className="text-[19px] font-bold text-white tracking-tight">Routine details</h2>
      </div>
      <button onClick={() => setDialog("delete")} className="text-red-200 text-sm font-semibold">
        Delete
      </button>
    </div>
  )

  return (
    <PageShell hero={hero}>
      {error && <div className="mb-3"><Alert type="error" message={error} /></div>}

      <div className="space-y-3">
        {/* Hero card */}
        <Card>
          <div className="px-6 py-5 text-center border-b border-gray-50">
            <div className="text-4xl mb-2">🛒</div>
            <h2 className="text-lg font-bold text-gray-900">{routine.name}</h2>
            <div className="mt-2 flex items-center justify-center gap-2">
              <span className={`text-xs font-semibold px-3 py-1 rounded-full ${STATUS_CHIP[routine.status] ?? "bg-gray-100 text-gray-500"}`}>
                {routine.status.charAt(0).toUpperCase() + routine.status.slice(1)}
              </span>
              {routine.runs_remaining !== null && (
                <span className="text-xs text-gray-400">{routine.runs_remaining} runs left</span>
              )}
              {routine.runs_remaining === null && (
                <span className="text-xs text-gray-400">Ongoing</span>
              )}
            </div>
          </div>

          {/* Summary */}
          <div className="divide-y divide-gray-50">
            {[
              ["Items", routine.items.map(i => `${i.item_name} ×${i.quantity}`).join(", ")],
              ["Frequency", freqLabel(routine)],
              ["Schedule time", routine.schedule_time_ist],
              ...(routine.end_date ? [["Ends", fmtDate(routine.end_date)]] : []),
            ].map(([label, val]) => (
              <div key={label} className="flex justify-between px-6 py-3 text-sm">
                <span className="text-gray-500">{label}</span>
                <span className="font-medium text-gray-800 text-right max-w-[55%]">{val}</span>
              </div>
            ))}
          </div>

          <div className="px-6 pb-4 pt-2">
            <Button variant="secondary" onClick={() => router.push(`/routines/${id}/edit`)}>
              Edit routine
            </Button>
          </div>
        </Card>

        {/* Paused banner */}
        {isPaused && (
          <div className="bg-amber-50 border border-amber-200 rounded-2xl px-5 py-4">
            <p className="text-sm font-semibold text-amber-800">Routine paused</p>
            <p className="text-xs text-amber-700 mt-1">
              No orders will be placed until you resume.
              {routine.end_date && ` End date extended to ${fmtDate(routine.end_date)}.`}
            </p>
          </div>
        )}

        {/* Upcoming runs */}
        {isActive && routine.upcoming_runs.length > 0 && (
          <Card>
            <div className="px-6 py-4">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Upcoming orders</p>
              <div className="space-y-3">
                {routine.upcoming_runs.map((run, idx) => (
                  <div key={run} className="flex items-center gap-3">
                    <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${idx === 0 ? "bg-[#2D6A4F]" : "bg-[#B7DFC6]"}`} />
                    <div className="flex-1">
                      <span className="text-sm text-gray-700">{fmtDateTime(run)}</span>
                    </div>
                    {idx === 0 && (
                      <button
                        className="text-xs text-[#2D6A4F] font-medium"
                        onClick={handleSkipNext}
                        disabled={actionLoading === "skip"}
                      >
                        {actionLoading === "skip" ? "…" : "Skip"}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </Card>
        )}

        {/* Past runs */}
        {runs.length > 0 && (
          <Card>
            <div className="px-6 py-4">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Past orders</p>
              <div className="space-y-1">
                {runs.filter(r => r.status !== "skipped" || r.skip_reason === "user_skip").slice(0, 5).map(run => {
                  const canExpand = (run.status === "placed" || run.status === "partial") && !!run.order_id
                  const isOpen = expandedRunId === run.id
                  return (
                    <div key={run.id}>
                      <div
                        className={`flex justify-between text-sm py-2 rounded-lg px-2 -mx-2 ${canExpand ? "cursor-pointer hover:bg-gray-50" : ""}`}
                        onClick={() => canExpand && setExpandedRunId(isOpen ? null : run.id)}
                      >
                        <span className="text-gray-600">{fmtDate(run.scheduled_at)}</span>
                        <div className="flex items-center gap-2">
                          <span className={
                            run.status === "placed" ? "text-[#2D6A4F] font-medium" :
                            run.status === "partial" ? "text-amber-600 font-medium" :
                            "text-gray-400"
                          }>
                            {run.status === "placed" ? `₹${run.total_amount?.toFixed(0)}` :
                             run.status === "partial" ? "Partial" :
                             run.status === "skipped" ? "Skipped" : "Failed"}
                          </span>
                          {canExpand && <span className="text-gray-300 text-xs">{isOpen ? "▲" : "▼"}</span>}
                        </div>
                      </div>
                      {isOpen && run.order_id && (
                        <div className="mt-2 mb-1">
                          <NutritionCard orderId={run.order_id} />
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          </Card>
        )}
      </div>

      {/* Footer CTA */}
      <div className="mt-4 space-y-2">
        {isActive && (
          <Button variant="secondary" onClick={() => setDialog("pause")} loading={actionLoading === "pause"}>
            Pause routine
          </Button>
        )}
        {isPaused && (
          <Button onClick={handleResume} loading={actionLoading === "resume"}
            className="bg-amber-500 hover:bg-amber-600 border-amber-500">
            Resume routine
          </Button>
        )}
      </div>

      {/* Delete dialog */}
      {dialog === "delete" && (
        <div className="fixed inset-0 bg-black/40 flex items-end justify-center p-4 z-50">
          <div className="bg-white rounded-3xl w-full max-w-sm p-6 space-y-4 shadow-2xl">
            <h3 className="font-bold text-gray-900">Delete routine?</h3>
            <p className="text-sm text-gray-500">
              This permanently stops all future runs. Past order history is preserved.
            </p>
            <div className="flex gap-3">
              <button onClick={() => setDialog(null)}
                className="flex-1 py-3 rounded-2xl bg-gray-100 text-gray-700 font-semibold text-sm">
                Cancel
              </button>
              <button onClick={handleDelete} disabled={actionLoading === "delete"}
                className="flex-1 py-3 rounded-2xl bg-red-600 text-white font-semibold text-sm">
                {actionLoading === "delete" ? "Deleting…" : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Pause dialog */}
      {dialog === "pause" && (
        <div className="fixed inset-0 bg-black/40 flex items-end justify-center p-4 z-50">
          <div className="bg-white rounded-3xl w-full max-w-sm p-6 space-y-4 shadow-2xl">
            <h3 className="font-bold text-gray-900">Pause routine?</h3>
            <p className="text-sm text-gray-500">
              No orders will be placed until you resume. Your end date will be extended to compensate.
            </p>
            <div className="flex gap-3">
              <button onClick={() => setDialog(null)}
                className="flex-1 py-3 rounded-2xl bg-gray-100 text-gray-700 font-semibold text-sm">
                Cancel
              </button>
              <button onClick={handlePause} disabled={actionLoading === "pause"}
                className="flex-1 py-3 rounded-2xl bg-gray-800 text-white font-semibold text-sm">
                {actionLoading === "pause" ? "Pausing…" : "Pause"}
              </button>
            </div>
          </div>
        </div>
      )}
    </PageShell>
  )
}
