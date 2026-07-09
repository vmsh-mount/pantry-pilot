"use client"

import { useEffect, useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import { api, type RunSummary, type RunItem } from "@/lib/api"
import { AppShell, Card, Spinner, Alert, Button } from "@/components/ui"

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null) {
  if (!iso) return "—"
  return new Date(iso).toLocaleDateString("en-IN", {
    weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit",
  })
}

const ACTIVE_STATES = new Set(["pending", "sensing", "planning", "optimizing", "confirmed", "placing"])

function badgeLabel(state: string): string {
  if (ACTIVE_STATES.has(state)) return "in_progress"
  return state
}

const STAGE_LABELS: Record<string, string> = {
  pending:    "Queued…",
  sensing:    "Checking pantry…",
  planning:   "Planning…",
  optimizing: "Optimising…",
  confirmed:  "Placing order…",
  placing:    "Placing order…",
}

// ── Status badge ─────────────────────────────────────────────────────────────

function StatusBadge({ state }: { state: string }) {
  const badge = badgeLabel(state)
  const styles: Record<string, string> = {
    in_progress:           "bg-amber-50 text-amber-700 border-amber-200",
    awaiting_confirmation: "bg-purple-50 text-purple-700 border-purple-200",
    completed:             "bg-[#D8F3DC] text-[#2D6A4F] border-[#2D6A4F]/20",
    failed:                "bg-red-50 text-red-600 border-red-200",
    skipped:               "bg-gray-100 text-gray-500 border-gray-200",
  }
  const labels: Record<string, string> = {
    in_progress:           "In progress",
    awaiting_confirmation: "Awaiting you",
    completed:             "Completed",
    failed:                "Failed",
    skipped:               "Skipped",
  }
  return (
    <span className={`shrink-0 px-2 py-0.5 rounded-full text-[11px] font-semibold border ${styles[badge] ?? "bg-gray-100 text-gray-500 border-gray-200"}`}>
      {labels[badge] ?? badge}
    </span>
  )
}

// ── Pipeline stage bar ────────────────────────────────────────────────────────

function PipelineBar({ state }: { state: string }) {
  const stages = ["sensing", "planning", "optimizing"]
  const active = stages.indexOf(state)
  if (active === -1) return null
  return (
    <div className="flex items-center gap-1 mt-2">
      {stages.map((s, i) => (
        <div
          key={s}
          className={`h-1 flex-1 rounded-full transition-all ${i <= active ? "bg-amber-400" : "bg-gray-200"}`}
        />
      ))}
    </div>
  )
}

// ── Expandable run row ────────────────────────────────────────────────────────

function RunRow({
  run,
  onRetry,
}: {
  run:     RunSummary
  onRetry: () => void
}) {
  const [open,    setOpen]    = useState(false)
  const [items,   setItems]   = useState<RunItem[] | null>(null)
  const [loading, setLoading] = useState(false)

  const isActive    = ACTIVE_STATES.has(run.state)
  const isFailed    = run.state === "failed"
  const isExpandable = !isActive

  async function toggleExpand() {
    if (!isExpandable) return
    if (open) { setOpen(false); return }
    setOpen(true)
    if (items !== null) return
    setLoading(true)
    const res = await api.runs.getItems(run.id)
    setLoading(false)
    if (res.success && res.data) {
      setItems((res.data as { items: RunItem[] }).items)
    }
  }

  return (
    <div className="border-b border-gray-50 last:border-0">
      <div
        className={`flex items-center gap-3 px-4 py-3 ${isExpandable ? "cursor-pointer hover:bg-gray-50" : ""}`}
        onClick={toggleExpand}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-gray-900">{fmtDate(run.triggered_at)}</span>
            <StatusBadge state={run.state} />
          </div>
          {isActive && (
            <div>
              <p className="text-xs text-gray-400 mt-0.5">{STAGE_LABELS[run.state] ?? "Running…"}</p>
              <PipelineBar state={run.state} />
            </div>
          )}
          {isFailed && run.failure_reason && (
            <p className="text-xs text-red-400 mt-0.5 truncate">{run.failure_reason}</p>
          )}
          {run.state === "skipped" && run.skip_reason && (
            <p className="text-xs text-gray-400 mt-0.5">
              {run.skip_reason === "user_skipped" ? "You skipped" : run.skip_reason}
            </p>
          )}
        </div>

        <div className="text-right shrink-0">
          {run.total_price != null && (
            <p className="text-sm font-semibold text-gray-900">₹{Math.round(run.total_price).toLocaleString("en-IN")}</p>
          )}
          {run.item_count > 0 && (
            <p className="text-xs text-gray-400">{run.item_count} items</p>
          )}
          {isExpandable && (
            <span className="text-gray-300 text-xs">{open ? "▲" : "▼"}</span>
          )}
        </div>
      </div>

      {/* Expanded item list */}
      {open && (
        <div className="px-4 pb-3">
          {loading ? (
            <div className="flex justify-center py-4"><Spinner /></div>
          ) : items && items.length > 0 ? (
            <div className="bg-gray-50 rounded-xl overflow-hidden divide-y divide-gray-100">
              {items.map((item, i) => (
                <div key={i} className="flex items-center justify-between px-3 py-2">
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium text-gray-800 truncate">
                      {item.swiggy_product_name || item.item_name}
                    </p>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      {item.brand && (
                        <span className="text-[10px] text-gray-400">{item.brand}</span>
                      )}
                      {item.is_substitution && (
                        <span className="text-[10px] px-1 py-0 rounded bg-amber-50 text-amber-600 border border-amber-200">
                          sub
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="text-right shrink-0 ml-2">
                    {item.total_price != null && (
                      <p className="text-xs font-semibold text-gray-800">₹{Math.round(item.total_price)}</p>
                    )}
                    <p className="text-[10px] text-gray-400">{item.quantity} {item.unit}</p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-gray-400 py-2 text-center">No items recorded for this run.</p>
          )}

          {isFailed && (
            <button
              onClick={(e) => { e.stopPropagation(); onRetry() }}
              className="mt-2 w-full text-xs py-2 rounded-xl border border-red-200 text-red-600 hover:bg-red-50 transition-colors"
            >
              Retry this run
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

const STATUS_OPTIONS = [
  { label: "All",          value: "" },
  { label: "In progress",  value: "in_progress" },
  { label: "Awaiting you", value: "awaiting_confirmation" },
  { label: "Completed",    value: "completed" },
  { label: "Failed",       value: "failed" },
  { label: "Skipped",      value: "skipped" },
]

const PAGE_SIZE = 20

export default function RunsPage() {
  const router = useRouter()
  const [runs,          setRuns]          = useState<RunSummary[]>([])
  const [filteredCount, setFilteredCount] = useState(0)
  const [statusFilter,  setStatusFilter]  = useState("")
  const [loading,       setLoading]       = useState(true)
  const [loadingMore,   setLoadingMore]   = useState(false)
  const [error,         setError]         = useState("")
  const [retrying,      setRetrying]      = useState(false)

  const fetchRuns = useCallback(async (status: string, offset: number, append: boolean) => {
    if (offset === 0) setLoading(true)
    else setLoadingMore(true)

    const res = await api.runs.list({
      ...(status ? { status } : {}),
      limit: PAGE_SIZE,
      offset,
    })

    if (offset === 0) setLoading(false)
    else setLoadingMore(false)

    if (!res.success) {
      if ((res.error as { code?: string })?.code === "NOT_AUTHENTICATED") {
        router.push("/")
        return
      }
      setError((res.error as { message?: string })?.message ?? "Could not load runs.")
      return
    }

    const data = res.data as import("@/lib/api").RunsListResponse
    setFilteredCount(data.filtered_count)
    if (append) {
      setRuns((prev) => [...prev, ...data.runs])
    } else {
      setRuns(data.runs)
    }
  }, [])

  useEffect(() => {
    fetchRuns(statusFilter, 0, false)
  }, [statusFilter, fetchRuns])

  async function handleRetry() {
    setRetrying(true)
    const res = await api.basket.trigger()
    setRetrying(false)
    if (res.success) {
      router.push("/dashboard")
    } else {
      setError((res.error as { message?: string })?.message ?? "Could not start a new run.")
    }
  }

  function handleLoadMore() {
    fetchRuns(statusFilter, runs.length, true)
  }

  const hasMore = runs.length < filteredCount

  return (
    <AppShell>
      <div className="flex items-center justify-between px-1 mb-4">
        <h1 className="text-white font-bold text-lg">Run history</h1>
        <button
          onClick={() => router.push("/dashboard")}
          className="text-[#D8F3DC] text-sm"
        >
          ← Back
        </button>
      </div>

      {error && <div className="mb-3"><Alert type="error" message={error} /></div>}

      {/* Status filter */}
      <div className="mb-3">
        <div className="flex gap-2 overflow-x-auto pb-1 no-scrollbar">
          {STATUS_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setStatusFilter(opt.value)}
              className={`shrink-0 px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                statusFilter === opt.value
                  ? "bg-white text-[#2D6A4F] border-white"
                  : "bg-transparent text-[#D8F3DC] border-[#D8F3DC]/40 hover:border-[#D8F3DC]"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-20 text-white"><Spinner size="lg" /></div>
      ) : runs.length === 0 ? (
        <Card>
          <div className="px-6 py-12 text-center space-y-3">
            <div className="text-5xl">📋</div>
            <h2 className="text-base font-bold text-gray-900">No runs yet</h2>
            <p className="text-sm text-gray-500">
              {statusFilter ? "No runs match this filter." : "Your planning runs will appear here."}
            </p>
            {statusFilter && (
              <Button variant="secondary" onClick={() => setStatusFilter("")}>
                Clear filter
              </Button>
            )}
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          <Card>
            <div className="divide-y divide-gray-50">
              {runs.map((run) => (
                <RunRow key={run.id} run={run} onRetry={handleRetry} />
              ))}
            </div>
          </Card>

          {hasMore && (
            <Button
              variant="secondary"
              onClick={handleLoadMore}
              loading={loadingMore}
              disabled={loadingMore}
            >
              Load more ({filteredCount - runs.length} remaining)
            </Button>
          )}
        </div>
      )}

      {retrying && (
        <div className="fixed inset-0 bg-black/20 flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl px-6 py-5 flex items-center gap-3 shadow-lg">
            <Spinner />
            <span className="text-sm font-medium text-gray-700">Starting new run…</span>
          </div>
        </div>
      )}
    </AppShell>
  )
}
