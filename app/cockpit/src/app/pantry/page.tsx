"use client"

import { useState, useEffect } from "react"
import Link from "next/link"
import { BottomNav, Spinner } from "@/components/ui"
import { api, PantryItemOut, PantryCounts, PantryStatus } from "@/lib/api"

// ── Icons ─────────────────────────────────────────────────────────────────────

function IconSettings() {
  return (
    <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.7)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  )
}

// ── Constants ─────────────────────────────────────────────────────────────────

const CATEGORY_ORDER = ["staples", "dairy", "fresh_produce", "packaged", "grocery"]
const CATEGORY_LABELS: Record<string, string> = {
  staples:       "Staples",
  dairy:         "Dairy",
  fresh_produce: "Fresh Produce",
  packaged:      "Packaged",
  grocery:       "Grocery",
}

const STATUS_COLOR: Record<PantryStatus, string> = {
  depleted: "#C0392B",
  low:      "#C87941",
  stocked:  "#40916C",
}

const STATUS_ORDER: Record<PantryStatus, number> = { depleted: 0, low: 1, stocked: 2 }

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtQty(qty: number, unit: string): string {
  if (qty <= 0)   return `0 ${unit}`
  if (qty < 0.1)  return `< 0.1 ${unit}`
  if (qty >= 10)  return `${Math.round(qty)} ${unit}`
  return `${Math.round(qty * 10) / 10} ${unit}`
}

function getStep(unit: string): number {
  if (unit === "kg" || unit === "L")   return 0.25
  if (unit === "g"  || unit === "ml")  return 50
  return 1
}

function stockPct(item: PantryItemOut): number {
  const qty = item.estimated_qty_remaining
  if (qty <= 0) return 0
  if (item.last_ordered_qty && item.last_ordered_qty > 0) {
    return Math.min(1, qty / item.last_ordered_qty)
  }
  if (item.reorder_threshold > 0) {
    return Math.min(1, qty / (item.reorder_threshold * 4))
  }
  return 0
}

function groupItems(items: PantryItemOut[]): [string, PantryItemOut[]][] {
  const buckets: Record<string, PantryItemOut[]> = {}
  for (const item of items) {
    const cat = CATEGORY_ORDER.includes(item.category) ? item.category : "other"
    ;(buckets[cat] ??= []).push(item)
  }
  for (const list of Object.values(buckets)) {
    list.sort((a, b) => {
      const sd = STATUS_ORDER[a.status] - STATUS_ORDER[b.status]
      return sd !== 0 ? sd : a.item_name.localeCompare(b.item_name)
    })
  }
  return [...CATEGORY_ORDER, "other"]
    .filter((cat) => buckets[cat]?.length)
    .map((cat) => [cat, buckets[cat]])
}

function recount(items: PantryItemOut[]): PantryCounts {
  return {
    total:    items.length,
    low:      items.filter((i) => i.status === "low").length,
    depleted: items.filter((i) => i.status === "depleted").length,
  }
}

// ── Item row ──────────────────────────────────────────────────────────────────

type SaveAction = "save" | "empty" | "remove" | null

function ItemRow({
  item,
  isLast,
  isExpanded,
  editQty,
  savingAction,
  saveError,
  onToggle,
  onQtyChange,
  onSave,
  onMarkEmpty,
  onRemove,
}: {
  item: PantryItemOut
  isLast: boolean
  isExpanded: boolean
  editQty: number
  savingAction: SaveAction
  saveError: string | null
  onToggle: () => void
  onQtyChange: (v: number) => void
  onSave: () => void
  onMarkEmpty: () => void
  onRemove: () => void
}) {
  const color = STATUS_COLOR[item.status]
  const pct   = stockPct(item) * 100
  const step  = getStep(item.standard_unit)
  const busy  = savingAction !== null

  return (
    <div>
      {/* Collapsed row */}
      <button
        onClick={onToggle}
        className="w-full text-left"
        style={{ padding: "12px 14px", display: "flex", alignItems: "center", gap: 10 }}
      >
        <div style={{ width: 8, height: 8, borderRadius: "50%", background: color, flexShrink: 0 }} />

        <span style={{ flex: 1, fontSize: 13, fontWeight: 500, color: "#1C1C1E", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {item.item_name}
        </span>

        <div style={{ width: 64, height: 3, borderRadius: 99, background: "rgba(0,0,0,.07)", flexShrink: 0, overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${pct}%`, background: color, opacity: 0.75, borderRadius: 99 }} />
        </div>

        <span style={{ fontSize: 12, color: "#8E8E93", minWidth: 52, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
          {fmtQty(item.estimated_qty_remaining, item.standard_unit)}
        </span>

        <span style={{ fontSize: 11, color: "#C7C7CC", flexShrink: 0, marginLeft: 2 }}>
          {isExpanded ? "∧" : "∨"}
        </span>
      </button>

      {/* Inline editor */}
      {isExpanded && (
        <div style={{ borderTop: "0.5px solid rgba(0,0,0,.05)", padding: "10px 14px 14px" }}>
          {/* Stepper */}
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 11, color: "#8E8E93", width: 64, flexShrink: 0 }}>Quantity</span>
            <div style={{
              display: "flex", alignItems: "center",
              border: "1px solid rgba(0,0,0,.10)",
              background: "#FAFAFA",
              borderRadius: 10,
              overflow: "hidden",
              pointerEvents: busy ? "none" : "auto",
              opacity: busy ? 0.5 : 1,
            }}>
              <button
                onClick={() => onQtyChange(Math.max(0, Math.round((editQty - step) * 1000) / 1000))}
                style={{ width: 36, height: 36, color: "#2D6A4F", fontSize: 18, display: "flex", alignItems: "center", justifyContent: "center" }}
              >
                −
              </button>
              <input
                type="number"
                value={editQty}
                onChange={(e) => onQtyChange(Math.max(0, parseFloat(e.target.value) || 0))}
                step={step}
                min={0}
                style={{ width: 56, textAlign: "center", fontSize: 14, fontWeight: 600, color: "#1C1C1E", border: "none", background: "transparent", outline: "none" }}
              />
              <button
                onClick={() => onQtyChange(Math.round((editQty + step) * 1000) / 1000)}
                style={{ width: 36, height: 36, color: "#2D6A4F", fontSize: 18, display: "flex", alignItems: "center", justifyContent: "center" }}
              >
                +
              </button>
            </div>
            <span style={{ fontSize: 12, color: "#8E8E93" }}>{item.standard_unit}</span>
          </div>

          {/* Save error */}
          {saveError && (
            <p style={{ fontSize: 11, color: "#C0392B", marginTop: 8 }}>{saveError}</p>
          )}

          {/* Mark Empty + Save */}
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button
              onClick={onMarkEmpty}
              disabled={busy}
              style={{
                flex: 1, padding: "8px 0", borderRadius: 10,
                background: "rgba(200,121,65,.10)",
                border: "1px solid rgba(200,121,65,.20)",
                color: "#C87941", fontSize: 12, fontWeight: 600,
                opacity: busy ? 0.6 : 1,
              }}
            >
              {savingAction === "empty" ? "Saving…" : "Mark Empty"}
            </button>
            <button
              onClick={onSave}
              disabled={busy}
              style={{
                flex: 1, padding: "8px 0", borderRadius: 10,
                background: "#2D6A4F", color: "white", fontSize: 12, fontWeight: 600,
                opacity: busy ? 0.6 : 1,
              }}
            >
              {savingAction === "save" ? "Saving…" : "Save"}
            </button>
          </div>

          {/* Remove */}
          <button
            onClick={onRemove}
            disabled={busy}
            style={{
              width: "100%", marginTop: 8, padding: "8px 0", borderRadius: 10,
              color: "#8E8E93", background: "rgba(0,0,0,.05)",
              border: "1px solid rgba(0,0,0,.08)", fontSize: 12,
              opacity: busy ? 0.6 : 1,
            }}
          >
            {savingAction === "remove" ? "Removing…" : "Remove item"}
          </button>
        </div>
      )}

      {!isLast && (
        <div style={{ height: "0.5px", background: "rgba(0,0,0,.05)", marginLeft: 14 }} />
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function PantryPage() {
  const [items,        setItems]        = useState<PantryItemOut[]>([])
  const [counts,       setCounts]       = useState<PantryCounts>({ total: 0, low: 0, depleted: 0 })
  const [loading,      setLoading]      = useState(true)
  const [fetchError,   setFetchError]   = useState<string | null>(null)
  const [expandedId,   setExpandedId]   = useState<string | null>(null)
  const [editQty,      setEditQty]      = useState(0)
  const [savingAction, setSavingAction] = useState<SaveAction>(null)
  const [saveError,    setSaveError]    = useState<string | null>(null)

  useEffect(() => {
    api.pantry.list().then((res) => {
      if (res.success && res.data) {
        setItems(res.data.items)
        setCounts(res.data.counts)
      } else {
        setFetchError("Could not load pantry items")
      }
      setLoading(false)
    })
  }, [])

  function toggleExpand(item: PantryItemOut) {
    if (expandedId === item.id) {
      setExpandedId(null)
    } else {
      setExpandedId(item.id)
      setEditQty(item.estimated_qty_remaining)
      setSavingAction(null)
      setSaveError(null)
    }
  }

  async function handleSave(itemId: string) {
    setSavingAction("save")
    setSaveError(null)
    try {
      const res = await api.pantry.update(itemId, { estimated_qty_remaining: editQty })
      if (res.success && res.data) {
        const updated = res.data.item
        const newItems = items.map((i) => (i.id === itemId ? updated : i))
        setItems(newItems)
        setCounts(recount(newItems))
        setExpandedId(null)
      } else {
        setSaveError(res.error?.message ?? "Failed to save. Try again.")
      }
    } catch {
      setSaveError("Network error. Try again.")
    } finally {
      setSavingAction(null)
    }
  }

  async function handleMarkEmpty(itemId: string) {
    setSavingAction("empty")
    setSaveError(null)
    try {
      const res = await api.pantry.update(itemId, { estimated_qty_remaining: 0 })
      if (res.success && res.data) {
        const updated = res.data.item
        const newItems = items.map((i) => (i.id === itemId ? updated : i))
        setItems(newItems)
        setCounts(recount(newItems))
        setExpandedId(null)
      } else {
        setSaveError(res.error?.message ?? "Failed to save. Try again.")
      }
    } catch {
      setSaveError("Network error. Try again.")
    } finally {
      setSavingAction(null)
    }
  }

  async function handleRemove(itemId: string) {
    setSavingAction("remove")
    setSaveError(null)
    try {
      const res = await api.pantry.remove(itemId)
      if (res.success) {
        const newItems = items.filter((i) => i.id !== itemId)
        setItems(newItems)
        setCounts(recount(newItems))
        setExpandedId(null)
      } else {
        setSaveError(res.error?.message ?? "Failed to remove. Try again.")
      }
    } catch {
      setSaveError("Network error. Try again.")
    } finally {
      setSavingAction(null)
    }
  }

  const grouped = groupItems(items)
  const showHeroDivider = counts.total > 0

  return (
    <main className="min-h-screen bg-[#F4F4F4] flex flex-col items-center">

      {/* ── HERO ── */}
      <div className="w-full bg-[#2D6A4F]">
        <div className="w-full max-w-[390px] mx-auto px-4 pt-6 pb-6">

          {/* Logo bar */}
          <div className="flex items-center justify-between mb-5">
            <span style={{ fontSize: 17, fontWeight: 700, color: "white" }}>🥦 PantryPilot</span>
            <Link href="/settings">
              <IconSettings />
            </Link>
          </div>

          {/* Label */}
          <p style={{ fontSize: 11, fontWeight: 700, color: "rgba(255,255,255,.45)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
            PANTRY
          </p>

          {/* Count */}
          <p style={{ fontSize: 34, fontWeight: 900, color: "white", letterSpacing: "-1px", marginTop: 2 }}>
            {loading ? "—" : `${counts.total} items`}
          </p>

          {/* Stats footer */}
          {showHeroDivider && (
            <>
              <div style={{ height: "0.5px", background: "rgba(255,255,255,0.18)", margin: "12px 0 10px" }} />
              {(counts.low > 0 || counts.depleted > 0) ? (
                <div style={{ display: "flex", gap: 24 }}>
                  {counts.low > 0 && (
                    <div>
                      <p style={{ fontSize: 18, fontWeight: 800, color: "rgba(255,255,255,.9)" }}>{counts.low}</p>
                      <p style={{ fontSize: 11, color: "rgba(255,255,255,.4)", marginTop: 1 }}>running low</p>
                    </div>
                  )}
                  {counts.depleted > 0 && (
                    <div>
                      <p style={{ fontSize: 18, fontWeight: 800, color: "rgba(255,255,255,.9)" }}>{counts.depleted}</p>
                      <p style={{ fontSize: 11, color: "rgba(255,255,255,.4)", marginTop: 1 }}>depleted</p>
                    </div>
                  )}
                </div>
              ) : (
                <p style={{ fontSize: 13, color: "rgba(255,255,255,.7)" }}>All items stocked ✓</p>
              )}
            </>
          )}
        </div>
      </div>

      {/* ── BODY ── */}
      <div className="w-full max-w-[390px] mx-auto px-3 pt-3 pb-28">

        {loading && (
          <div className="flex justify-center pt-16">
            <Spinner />
          </div>
        )}

        {!loading && fetchError && (
          <div className="text-center pt-16">
            <p style={{ fontSize: 15, fontWeight: 600, color: "#8E8E93" }}>{fetchError}</p>
          </div>
        )}

        {!loading && !fetchError && counts.total === 0 && (
          <div className="text-center" style={{ paddingTop: 64 }}>
            <p style={{ fontSize: 15, fontWeight: 600, color: "#8E8E93" }}>No pantry items yet</p>
            <p style={{ fontSize: 13, color: "#AEAEB2", marginTop: 6 }}>
              Items appear here after your first Flow run
            </p>
          </div>
        )}

        {!loading && !fetchError && grouped.map(([cat, catItems]) => (
          <div key={cat} style={{ marginBottom: 16 }}>
            <p style={{
              fontSize: 11, fontWeight: 700, textTransform: "uppercase",
              letterSpacing: "0.1em", color: "#8E8E93",
              marginBottom: 6, paddingLeft: 4,
            }}>
              {CATEGORY_LABELS[cat] ?? "Other"}
            </p>
            <div className="bg-white rounded-2xl overflow-hidden">
              {catItems.map((item, idx) => (
                <ItemRow
                  key={item.id}
                  item={item}
                  isLast={idx === catItems.length - 1}
                  isExpanded={expandedId === item.id}
                  editQty={expandedId === item.id ? editQty : item.estimated_qty_remaining}
                  savingAction={expandedId === item.id ? savingAction : null}
                  saveError={expandedId === item.id ? saveError : null}
                  onToggle={() => toggleExpand(item)}
                  onQtyChange={setEditQty}
                  onSave={() => handleSave(item.id)}
                  onMarkEmpty={() => handleMarkEmpty(item.id)}
                  onRemove={() => handleRemove(item.id)}
                />
              ))}
            </div>
          </div>
        ))}
      </div>

      <BottomNav />
    </main>
  )
}
