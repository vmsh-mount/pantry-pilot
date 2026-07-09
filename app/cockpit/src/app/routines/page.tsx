"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { api } from "@/lib/api"
import { AppShell, Card, Button, Spinner, Alert } from "@/components/ui"

interface RoutineItem { id: string; item_name: string; quantity: number; unit: string }
interface Routine {
  id: string
  name: string
  status: string
  frequency_type: string
  frequency_value: number
  schedule_time_ist: string
  next_run_at: string | null
  runs_remaining: number | null
  items: RoutineItem[]
}

function frequencyLabel(r: Routine): string {
  if (r.frequency_type === "every_n_days") {
    if (r.frequency_value === 1) return "Every day"
    if (r.frequency_value === 7) return "Every week"
    if (r.frequency_value === 14) return "Every 2 weeks"
    return `Every ${r.frequency_value} days`
  }
  if (r.frequency_type === "weekly") {
    const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return `Every ${days[r.frequency_value] ?? "week"}`
  }
  if (r.frequency_type === "monthly") return `Monthly (day ${r.frequency_value})`
  return ""
}

function nextRunLabel(next: string | null): string {
  if (!next) return ""
  const d = new Date(next)
  const now = new Date()
  const diffMs = d.getTime() - now.getTime()
  const diffH = diffMs / (1000 * 60 * 60)
  if (diffH < 1) return "in less than 1h"
  if (diffH < 24) return `today ${d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}`
  if (diffH < 48) return `tomorrow ${d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}`
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" })
}

const STATUS_CHIP: Record<string, string> = {
  active: "bg-[#D8F3DC] text-[#1B4332]",
  paused: "bg-amber-100 text-amber-800",
  ended:  "bg-gray-100 text-gray-500",
}

export default function RoutinesPage() {
  const router = useRouter()
  const [routines, setRoutines] = useState<Routine[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState("")

  useEffect(() => {
    api.routines.list().then((res) => {
      setLoading(false)
      if (res.success && res.data) {
        setRoutines(res.data as Routine[])
      } else if (res.error?.code === "NOT_AUTHENTICATED") {
        router.replace("/")
      } else {
        setError("Could not load routines.")
      }
    })
  }, [])

  const active  = routines.filter(r => r.status === "active")
  const paused  = routines.filter(r => r.status === "paused")
  const ended   = routines.filter(r => r.status === "ended")

  return (
    <AppShell>
      <div className="flex items-center justify-between px-1 mb-4">
        <h1 className="text-white font-bold text-lg">Routines</h1>
        <button
          onClick={() => router.push("/routines/new")}
          className="text-[#D8F3DC] text-sm font-semibold"
        >
          + New
        </button>
      </div>

      {loading && (
        <div className="flex justify-center py-16">
          <Spinner size="lg" />
        </div>
      )}

      {error && <Alert type="error" message={error} />}

      {!loading && routines.length === 0 && (
        <Card>
          <div className="px-6 py-12 flex flex-col items-center gap-4 text-center">
            <span className="text-5xl">🔄</span>
            <div>
              <p className="font-semibold text-gray-800">No routines yet</p>
              <p className="text-sm text-gray-500 mt-1">
                Set up a routine to place recurring orders automatically.
              </p>
            </div>
            <Button onClick={() => router.push("/routines/new")}>Create your first routine</Button>
          </div>
        </Card>
      )}

      {!loading && routines.length > 0 && (
        <div className="space-y-3">
          {[...active, ...paused, ...ended].map((r) => (
            <Card key={r.id}>
              <button
                className="w-full text-left px-5 py-4 flex items-start gap-3"
                onClick={() => router.push(`/routines/${r.id}`)}
              >
                <div className="w-10 h-10 rounded-xl bg-[#D8F3DC] flex items-center justify-center text-xl flex-shrink-0">
                  🛒
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-gray-900 text-sm truncate">{r.name}</span>
                    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full flex-shrink-0 ${STATUS_CHIP[r.status] ?? "bg-gray-100 text-gray-500"}`}>
                      {r.status.charAt(0).toUpperCase() + r.status.slice(1)}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 mt-0.5">
                    {frequencyLabel(r)} · {r.items.map(i => i.item_name).join(", ")}
                  </p>
                  {r.next_run_at && r.status === "active" && (
                    <p className="text-xs text-[#2D6A4F] font-medium mt-1">
                      Next: {nextRunLabel(r.next_run_at)}
                    </p>
                  )}
                  {r.status === "ended" && (
                    <p className="text-xs text-gray-400 mt-1">Ended</p>
                  )}
                </div>
              </button>
            </Card>
          ))}
        </div>
      )}
    </AppShell>
  )
}
