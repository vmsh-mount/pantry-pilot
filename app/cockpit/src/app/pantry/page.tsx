"use client"

import { useState, useEffect } from "react"
import { PageShell, HeroBrandBar, Spinner } from "@/components/ui"
import { api, PantryItemOut, PantryCounts, PantryStatus } from "@/lib/api"

// ── Constants ─────────────────────────────────────────────────────────────────

const CATEGORY_ORDER = ["staples", "dairy", "fresh_produce", "packaged", "grocery"]

interface CategoryMeta {
  label: string
  icon:  string
  bg:    string
  text:  string
}

// Five distinct hue families (yellow/tan, blue, green, purple, peach) so
// adjacent zones never read as "the same beige card" on a small screen.
// grocery/other reuses the peach tint — it's the catch-all bucket for
// anything Swiggy's category string doesn't map cleanly to the other four,
// so in practice it's one of the more common zones, not an edge case.
const CATEGORY_META: Record<string, CategoryMeta> = {
  staples:       { label: "Staples",       icon: "🌾", bg: "linear-gradient(165deg, #FBF3E3, #F3E4C8)", text: "#8A6423" },
  dairy:         { label: "Dairy",         icon: "🥛", bg: "linear-gradient(165deg, #EFF6FB, #DCEAF5)", text: "#2E5F82" },
  fresh_produce: { label: "Fresh Produce", icon: "🥬", bg: "linear-gradient(165deg, #F0F9F1, #D8F3DC)", text: "#1B4332" },
  packaged:      { label: "Packaged",      icon: "📦", bg: "linear-gradient(165deg, #F0EEF5, #E2DEEE)", text: "#5B4E7A" },
  grocery:       { label: "Grocery",       icon: "🛒", bg: "linear-gradient(165deg, #FCEEE4, #F7DCC7)", text: "#A0562A" },
  other:         { label: "Other",         icon: "🛒", bg: "linear-gradient(165deg, #FCEEE4, #F7DCC7)", text: "#A0562A" },
}

const STATUS_COLOR: Record<PantryStatus, string> = {
  depleted: "#C0392B",
  low:      "#C87941",
  stocked:  "#40916C",
}

const STATUS_ORDER: Record<PantryStatus, number> = { depleted: 0, low: 1, stocked: 2 }

// Best-effort keyword → icon lookup, matched with word boundaries (\b) rather
// than raw substring — raw substring would match "egg" inside "veggies" and
// render an egg icon on a vegetable item. No match falls back to the
// category icon, so every tile always has *some* icon.
const ITEM_ICON_KEYWORDS: [RegExp, string][] = [
  [/\brice\b/,                          "🍚"],
  [/\batta\b|\bwheat\b|\bflour\b/,       "🌾"],
  [/\bdal\b|\blentils?\b/,               "🫘"],
  [/\bsugar\b/,                          "🍬"],
  [/\bsalt\b/,                           "🧂"],
  [/\boil\b/,                            "🫒"],
  [/\bmilk\b/,                           "🥛"],
  [/\bcurd\b|\byog(h)?urt\b/,            "🥣"],
  [/\bbutter\b/,                         "🧈"],
  [/\bpaneer\b|\bcheese\b/,              "🧀"],
  [/\beggs?\b/,                          "🥚"],
  [/\bbread\b/,                          "🍞"],
  [/\bonions?\b/,                        "🧅"],
  [/\btomato(es)?\b/,                    "🍅"],
  [/\bpotato(es)?\b/,                    "🥔"],
  [/\bgarlic\b/,                         "🧄"],
  [/\bginger\b/,                         "🫚"],
  [/\bcarrots?\b/,                       "🥕"],
  [/\bcucumber\b/,                       "🥒"],
  [/\bspinach\b|\bveg(gies|etables?)?\b/, "🥬"],
  [/\bbananas?\b/,                       "🍌"],
  [/\bapples?\b/,                        "🍎"],
  [/\bbiscuits?\b|\bcookies?\b/,         "🍪"],
  [/\bcereal\b/,                         "🥣"],
  [/\bpasta\b|\bnoodles?\b/,             "🍝"],
  [/\bchips?\b/,                         "🍟"],
  [/\bchocolate\b/,                      "🍫"],
  [/\btea\b/,                            "🍵"],
  [/\bcoffee\b/,                         "☕"],
  [/\bwater\b/,                          "💧"],
  [/\bjuice\b/,                          "🧃"],
  [/\bhoney\b/,                          "🍯"],
  [/\bjam\b/,                            "🍓"],
  [/\bcans?\b|\bcanned\b|\bbeans?\b/,    "🥫"],
  [/\bsoap\b/,                           "🧴"],
  [/\btissues?\b|\bnapkins?\b/,          "🧻"],
  [/\bdetergent\b/,                      "🧼"],
  [/\bmasala\b|\bspices?\b|\bpowder\b/,  "🌶️"],
]

function iconFor(itemName: string, fallback: string): string {
  const name = itemName.toLowerCase()
  for (const [re, icon] of ITEM_ICON_KEYWORDS) {
    if (re.test(name)) return icon
  }
  return fallback
}

function categoryMeta(category: string): CategoryMeta {
  const cat = CATEGORY_ORDER.includes(category) ? category : "other"
  return CATEGORY_META[cat] ?? CATEGORY_META.other
}

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

function gaugeBackground(pct: number, color: string): string {
  const deg = Math.round(Math.min(1, Math.max(0, pct)) * 360)
  return `conic-gradient(${color} ${deg}deg, rgba(0,0,0,.08) 0)`
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

// ── Item tile ─────────────────────────────────────────────────────────────────

type SaveAction = "save" | "empty" | "remove" | null

function ItemTile({
  item,
  active,
  onClick,
}: {
  item: PantryItemOut
  active: boolean
  onClick: () => void
}) {
  const color = STATUS_COLOR[item.status]
  const pct   = stockPct(item)
  const meta  = categoryMeta(item.category)
  const icon  = iconFor(item.item_name, meta.icon)

  return (
    <button
      onClick={onClick}
      style={{
        background: "rgba(255,255,255,.7)",
        borderRadius: 12,
        padding: "9px 6px 7px",
        display: "flex", flexDirection: "column", alignItems: "center",
        position: "relative",
        outline: active ? "2px solid #2D6A4F" : "none",
        outlineOffset: -2,
      }}
    >
      <div style={{ position: "absolute", top: 6, right: 6, width: 6, height: 6, borderRadius: "50%", background: color }} />
      <div style={{
        width: 38, height: 38, borderRadius: "50%", marginBottom: 5,
        display: "flex", alignItems: "center", justifyContent: "center",
        background: gaugeBackground(pct, color),
      }}>
        <div style={{
          width: 29, height: 29, borderRadius: "50%", background: "#fff",
          display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13,
        }}>
          {icon}
        </div>
      </div>
      <span style={{ fontSize: 9.5, fontWeight: 650, color: "#1C1C1E", textAlign: "center", lineHeight: 1.15, maxWidth: 60 }}>
        {item.item_name}
      </span>
      <span style={{ fontSize: 8, color: "#8E8E93", marginTop: 2 }}>
        {fmtQty(item.estimated_qty_remaining, item.standard_unit)}
      </span>
    </button>
  )
}

function MoreTile({ count, onClick }: { count: number; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        background: "rgba(255,255,255,.4)",
        border: "1px dashed rgba(0,0,0,.15)",
        borderRadius: 12,
        padding: "9px 6px 7px",
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      }}
    >
      <div style={{
        width: 38, height: 38, borderRadius: "50%", marginBottom: 5,
        display: "flex", alignItems: "center", justifyContent: "center",
        background: "rgba(255,255,255,.7)",
      }}>
        <span style={{ fontSize: 13, fontWeight: 800, color: "#5A5A5F" }}>+{count}</span>
      </div>
      <span style={{ fontSize: 9.5, fontWeight: 700, color: "#5A5A5F" }}>more</span>
    </button>
  )
}

// ── Item editor — same fields/handlers as before, now anchored below the
//    zone's tile grid instead of directly under a list row ──────────────────

function ItemEditor({
  item,
  editQty,
  savingAction,
  saveError,
  onQtyChange,
  onSave,
  onMarkEmpty,
  onRemove,
}: {
  item: PantryItemOut
  editQty: number
  savingAction: SaveAction
  saveError: string | null
  onQtyChange: (v: number) => void
  onSave: () => void
  onMarkEmpty: () => void
  onRemove: () => void
}) {
  const step = getStep(item.standard_unit)
  const busy = savingAction !== null

  return (
    <div style={{ background: "#fff", borderRadius: 14, padding: "12px 14px 14px", marginTop: 10 }}>
      <p style={{ fontSize: 13, fontWeight: 700, color: "#1C1C1E", marginBottom: 10 }}>{item.item_name}</p>

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
  )
}

// ── Zone ──────────────────────────────────────────────────────────────────────

const ZONE_CAP     = 6  // total slots per zone before capping kicks in
const ZONE_VISIBLE = 5  // real tiles shown once capped (6th slot = "+N more")

function PantryZone({
  category,
  items,
  expandedId,
  editQty,
  savingAction,
  saveError,
  zoneExpanded,
  onExpandZone,
  onToggleItem,
  onQtyChange,
  onSave,
  onMarkEmpty,
  onRemove,
}: {
  category: string
  items: PantryItemOut[]
  expandedId: string | null
  editQty: number
  savingAction: SaveAction
  saveError: string | null
  zoneExpanded: boolean
  onExpandZone: () => void
  onToggleItem: (item: PantryItemOut) => void
  onQtyChange: (v: number) => void
  onSave: (itemId: string) => void
  onMarkEmpty: (itemId: string) => void
  onRemove: (itemId: string) => void
}) {
  const meta         = categoryMeta(category)
  const overflow      = items.length > ZONE_CAP
  const showMoreTile  = overflow && !zoneExpanded
  const visibleItems  = showMoreTile ? items.slice(0, ZONE_VISIBLE) : items
  const moreCount     = items.length - ZONE_VISIBLE
  const activeItem    = items.find((i) => i.id === expandedId)

  return (
    <div style={{ background: meta.bg, borderRadius: 16, padding: "12px 10px 14px", marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "0 4px 10px" }}>
        <span style={{ fontSize: 15 }}>{meta.icon}</span>
        <span style={{ fontSize: 12, fontWeight: 750, color: meta.text, flex: 1 }}>{meta.label}</span>
        <span style={{ fontSize: 10, fontWeight: 650, color: meta.text, opacity: 0.6 }}>{items.length}</span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
        {visibleItems.map((item) => (
          <ItemTile
            key={item.id}
            item={item}
            active={expandedId === item.id}
            onClick={() => onToggleItem(item)}
          />
        ))}
        {showMoreTile && <MoreTile count={moreCount} onClick={onExpandZone} />}
      </div>

      {activeItem && (
        <ItemEditor
          item={activeItem}
          editQty={editQty}
          savingAction={savingAction}
          saveError={saveError}
          onQtyChange={onQtyChange}
          onSave={() => onSave(activeItem.id)}
          onMarkEmpty={() => onMarkEmpty(activeItem.id)}
          onRemove={() => onRemove(activeItem.id)}
        />
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function PantryPage() {
  const [items,         setItems]         = useState<PantryItemOut[]>([])
  const [counts,        setCounts]        = useState<PantryCounts>({ total: 0, low: 0, depleted: 0 })
  const [loading,       setLoading]       = useState(true)
  const [fetchError,    setFetchError]    = useState<string | null>(null)
  const [expandedId,    setExpandedId]    = useState<string | null>(null)
  const [editQty,       setEditQty]       = useState(0)
  const [savingAction,  setSavingAction]  = useState<SaveAction>(null)
  const [saveError,     setSaveError]     = useState<string | null>(null)
  const [expandedZones, setExpandedZones] = useState<Set<string>>(new Set())

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

  function expandZone(cat: string) {
    setExpandedZones((prev) => new Set(prev).add(cat))
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

  const hero = (
    <>
      <HeroBrandBar />

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
    </>
  )

  return (
    <PageShell hero={hero}>

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
          <PantryZone
            key={cat}
            category={cat}
            items={catItems}
            expandedId={expandedId}
            editQty={editQty}
            savingAction={savingAction}
            saveError={saveError}
            zoneExpanded={expandedZones.has(cat)}
            onExpandZone={() => expandZone(cat)}
            onToggleItem={toggleExpand}
            onQtyChange={setEditQty}
            onSave={handleSave}
            onMarkEmpty={handleMarkEmpty}
            onRemove={handleRemove}
          />
        ))}
    </PageShell>
  )
}
