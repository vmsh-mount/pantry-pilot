"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import {
  api,
  type QuickBasketItem,
  type QuickSearchResult,
  type QuickOrderResult,
} from "@/lib/api"
import { AppShell, Spinner } from "@/components/ui"

type View = "search" | "basket" | "confirmed"

export default function QuickOrderPage() {
  const router = useRouter()
  const [view, setView]             = useState<View>("search")
  const [query, setQuery]           = useState("")
  const [searching, setSearching]   = useState(false)
  const [results, setResults]       = useState<QuickSearchResult[]>([])
  const [basket, setBasket]         = useState<QuickBasketItem[]>([])
  const [total, setTotal]           = useState(0)
  const [placing, setPlacing]       = useState(false)
  const [order, setOrder]           = useState<QuickOrderResult | null>(null)
  const [error, setError]           = useState<string | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [addingSkus, setAddingSkus] = useState<Set<string>>(new Set())
  const searchTimer                 = useRef<ReturnType<typeof setTimeout>>()
  const inputRef                    = useRef<HTMLInputElement>(null)

  // Load basket on mount
  useEffect(() => {
    api.quick.getBasket().then(res => {
      if (res.success && res.data) {
        setBasket(res.data.items)
        setTotal(res.data.estimated_total)
      } else if (!res.success) {
        setError("Could not load your basket. Please refresh.")
      }
    })
  }, [])

  // Debounced search
  useEffect(() => {
    clearTimeout(searchTimer.current)
    if (!query.trim()) { setResults([]); return }
    searchTimer.current = setTimeout(async () => {
      setSearching(true)
      setSearchError(null)
      const res = await api.quick.search(query)
      setSearching(false)
      if (res.success && res.data) {
        setResults(res.data.results)
      } else {
        setSearchError("Search failed. Check your connection.")
        setResults([])
      }
    }, 350)
    return () => clearTimeout(searchTimer.current)
  }, [query])

  async function addToBasket(item: QuickSearchResult) {
    if (!item.sku_id) return
    setAddingSkus(prev => new Set(prev).add(item.sku_id!))
    const res = await api.quick.addItem({
      item_name:  item.item_name,
      brand:      item.brand,
      sku_id:     item.sku_id,
      unit:       item.unit,
      quantity:   1,
      unit_price: item.unit_price,
    })
    setAddingSkus(prev => { const s = new Set(prev); s.delete(item.sku_id!); return s })
    if (res.success && res.data) {
      const updated = [...basket, res.data.item]
      setBasket(updated)
      setTotal(updated.reduce((s, i) => s + i.unit_price * i.quantity, 0))
    } else {
      setError("Could not add item. Please try again.")
    }
  }

  async function updateQty(id: string, qty: number) {
    if (qty < 1) return removeItem(id)
    const res = await api.quick.updateItem(id, { quantity: qty })
    if (res.success && res.data) {
      const updated = basket.map(i => i.id === id ? res.data!.item : i)
      setBasket(updated)
      setTotal(updated.reduce((s, i) => s + i.unit_price * i.quantity, 0))
    } else {
      setError("Could not update quantity. Please try again.")
    }
  }

  async function removeItem(id: string) {
    const res = await api.quick.removeItem(id)
    if (res.success) {
      const updated = basket.filter(i => i.id !== id)
      setBasket(updated)
      setTotal(updated.reduce((s, i) => s + i.unit_price * i.quantity, 0))
    } else {
      setError("Could not remove item. Please try again.")
    }
  }

  async function placeOrder() {
    setPlacing(true)
    setError(null)
    // No address passed — backend uses household's preferred_address_id.
    // Address picker (api.quick.addresses) is available for a future "change address" flow.
    const res = await api.quick.checkout()
    setPlacing(false)
    if (res.success && res.data) {
      setOrder(res.data)
      setBasket([])
      setTotal(0)
      setView("confirmed")
    } else {
      setError(res.error?.message ?? "Checkout failed. Please try again.")
    }
  }

  const basketCount = basket.reduce((s, i) => s + i.quantity, 0)

  // ── Confirmed ──────────────────────────────────────────────────────────────
  if (view === "confirmed" && order) {
    return (
      <AppShell>
        <div className="flex items-center gap-2 px-1 mb-6">
          <button onClick={() => router.push("/dashboard")} className="text-[#D8F3DC] text-lg">←</button>
          <span className="text-white font-bold text-lg">Order placed</span>
        </div>
        <div className="bg-white rounded-2xl p-5 space-y-4">
          <div className="text-center">
            <div className="text-4xl mb-2">✅</div>
            <p className="font-bold text-gray-900 text-lg">Order confirmed!</p>
            <p className="text-gray-500 text-sm mt-1">Swiggy ID: {order.swiggy_order_id}</p>
          </div>
          <div className="space-y-2">
            {order.items.map(i => (
              <div key={i.id} className="flex justify-between text-sm text-gray-700">
                <span>{i.item_name}{i.brand ? ` · ${i.brand}` : ""} ×{i.quantity}</span>
                <span>₹{(i.unit_price * i.quantity).toFixed(0)}</span>
              </div>
            ))}
          </div>
          <div className="border-t pt-3 space-y-1 text-sm">
            <div className="flex justify-between text-gray-600"><span>Items</span><span>₹{order.item_total.toFixed(0)}</span></div>
            {order.delivery_fee > 0 && <div className="flex justify-between text-gray-600"><span>Delivery</span><span>₹{order.delivery_fee.toFixed(0)}</span></div>}
            {order.taxes > 0 && <div className="flex justify-between text-gray-600"><span>Taxes</span><span>₹{order.taxes.toFixed(0)}</span></div>}
            <div className="flex justify-between font-bold text-gray-900 pt-1"><span>Total</span><span>₹{order.grand_total.toFixed(0)}</span></div>
          </div>
          <button
            onClick={() => router.push("/dashboard")}
            className="w-full bg-[#2D6A4F] text-white rounded-xl py-3 font-semibold text-sm"
          >
            Back to home
          </button>
        </div>
      </AppShell>
    )
  }

  // ── Basket ─────────────────────────────────────────────────────────────────
  if (view === "basket") {
    return (
      <AppShell>
        <div className="flex items-center gap-2 px-1 mb-6">
          <button onClick={() => setView("search")} className="text-[#D8F3DC] text-lg">←</button>
          <span className="text-white font-bold text-lg">Your basket</span>
        </div>

        {basket.length === 0 ? (
          <div className="text-center text-white/60 py-16">Basket is empty</div>
        ) : (
          <div className="space-y-3">
            {basket.map(item => (
              <div key={item.id} className="bg-white rounded-2xl px-4 py-3 flex items-center justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <p className="font-semibold text-gray-900 text-sm truncate">{item.item_name}</p>
                  {item.brand && <p className="text-gray-400 text-xs">{item.brand}</p>}
                  <p className="text-[#2D6A4F] text-xs mt-0.5">₹{item.unit_price} / {item.unit}</p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => updateQty(item.id, item.quantity - 1)}
                    className="w-7 h-7 rounded-full bg-gray-100 text-gray-600 font-bold text-base flex items-center justify-center"
                  >−</button>
                  <span className="w-5 text-center text-sm font-semibold text-gray-900">{item.quantity}</span>
                  <button
                    onClick={() => updateQty(item.id, item.quantity + 1)}
                    className="w-7 h-7 rounded-full bg-[#2D6A4F] text-white font-bold text-base flex items-center justify-center"
                  >+</button>
                </div>
                <span className="text-sm font-semibold text-gray-900 w-12 text-right shrink-0">
                  ₹{(item.unit_price * item.quantity).toFixed(0)}
                </span>
              </div>
            ))}

            <div className="bg-white/10 rounded-2xl px-4 py-3 flex justify-between text-white font-semibold">
              <span>Total</span>
              <span>₹{total.toFixed(0)}</span>
            </div>

            {error && (
              <div className="bg-red-50 text-red-700 text-sm rounded-xl px-4 py-3">{error}</div>
            )}

            <button
              onClick={placeOrder}
              disabled={placing}
              className="w-full bg-white text-[#2D6A4F] rounded-2xl py-4 font-bold text-base disabled:opacity-60"
            >
              {placing ? <Spinner size="sm" /> : `Confirm & Order · ₹${total.toFixed(0)}`}
            </button>
          </div>
        )}
      </AppShell>
    )
  }

  // ── Search ─────────────────────────────────────────────────────────────────
  return (
    <AppShell>
      {/* Header */}
      <div className="flex items-center gap-2 px-1 mb-5">
        <button onClick={() => router.push("/dashboard")} className="text-[#D8F3DC] text-lg">←</button>
        <span className="text-white font-bold text-lg">Quick Order</span>
      </div>

      {/* Search bar */}
      <div className="relative mb-4">
        <span className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400 text-lg">🔍</span>
        <input
          ref={inputRef}
          autoFocus
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search Swiggy Instamart…"
          className="w-full bg-white rounded-2xl pl-11 pr-4 py-3.5 text-sm text-gray-900 placeholder-gray-400 focus:outline-none shadow-sm"
        />
        {searching && (
          <span className="absolute right-4 top-1/2 -translate-y-1/2">
            <Spinner size="sm" />
          </span>
        )}
      </div>

      {/* Results */}
      <div className="space-y-2 pb-24">
        {searchError && (
          <p className="text-center text-red-300 text-sm py-4">{searchError}</p>
        )}
        {!searchError && results.length === 0 && query && !searching && (
          <p className="text-center text-white/50 text-sm py-8">No results for "{query}"</p>
        )}
        {results.map((r, idx) => {
          const inBasket = !!r.sku_id && basket.some(b => b.sku_id === r.sku_id)
          const isAdding = !!r.sku_id && addingSkus.has(r.sku_id)
          const noSku    = !r.sku_id
          const disabled = !r.in_stock || inBasket || isAdding || noSku
          return (
            <div key={r.sku_id ?? idx} className="bg-white rounded-2xl px-4 py-3 flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <p className="font-semibold text-gray-900 text-sm truncate">{r.item_name}</p>
                {r.brand && <p className="text-gray-400 text-xs">{r.brand}</p>}
                <p className="text-[#2D6A4F] text-xs mt-0.5">₹{r.unit_price} / {r.unit}</p>
              </div>
              <button
                onClick={() => addToBasket(r)}
                disabled={disabled}
                className={`shrink-0 px-4 py-2 rounded-xl text-sm font-semibold transition-colors ${
                  inBasket
                    ? "bg-gray-100 text-gray-400 cursor-default"
                    : r.in_stock && !noSku
                    ? "bg-[#2D6A4F] text-white"
                    : "bg-gray-100 text-gray-400 cursor-not-allowed"
                }`}
              >
                {isAdding ? "…" : inBasket ? "Added" : r.in_stock && !noSku ? "Add" : "OOS"}
              </button>
            </div>
          )
        })}
      </div>

      {/* Sticky basket chip */}
      {basketCount > 0 && (
        <div className="fixed bottom-6 left-0 right-0 px-6 z-10">
          <button
            onClick={() => setView("basket")}
            className="w-full bg-white text-[#2D6A4F] rounded-2xl py-4 font-bold text-base shadow-lg flex items-center justify-between px-5"
          >
            <span className="bg-[#2D6A4F] text-white text-xs font-bold rounded-full w-6 h-6 flex items-center justify-center">{basketCount}</span>
            <span>Review basket</span>
            <span>₹{total.toFixed(0)} →</span>
          </button>
        </div>
      )}
    </AppShell>
  )
}
