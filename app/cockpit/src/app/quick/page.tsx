"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import {
  api,
  type ProductSearchResult,
  type QuickBasketItem,
  type QuickOrderResult,
  type QuickRecentOrder,
} from "@/lib/api"
import {
  AppShell,
  Card,
  Button,
  Alert,
  Spinner,
} from "@/components/ui"
import { ItemSearchDropdown } from "@/components/basket/ItemSearchDropdown"

// ── Qty stepper ────────────────────────────────────────────────────────────────

function QtyStepper({
  value,
  onDecrement,
  onIncrement,
}: {
  value: number
  onDecrement: () => void
  onIncrement: () => void
}) {
  return (
    <div className="flex items-center gap-1.5">
      <button
        onClick={onDecrement}
        className="w-7 h-7 rounded-full bg-gray-100 text-gray-600 font-bold flex items-center justify-center text-base leading-none transition-colors hover:bg-gray-200"
      >
        −
      </button>
      <span className="w-5 text-center text-sm font-semibold text-gray-900 tabular-nums">{value}</span>
      <button
        onClick={onIncrement}
        className="w-7 h-7 rounded-full bg-[#2D6A4F] text-white font-bold flex items-center justify-center text-base leading-none transition-colors hover:bg-[#1B4332]"
      >
        +
      </button>
    </div>
  )
}

// ── Basket item row ────────────────────────────────────────────────────────────

function BasketRow({
  item,
  onQtyChange,
  onRemove,
}: {
  item: QuickBasketItem
  onQtyChange: (qty: number) => void
  onRemove: () => void
}) {
  const lineTotal = Math.round(item.unit_price * item.quantity)
  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-50 last:border-b-0">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-gray-900 truncate">{item.item_name}</p>
        {item.brand && <p className="text-xs text-gray-400">{item.brand}</p>}
        <p className="text-xs text-[#2D6A4F] font-medium mt-0.5">₹{item.unit_price} / {item.unit}</p>
      </div>
      <QtyStepper
        value={item.quantity}
        onDecrement={() => onQtyChange(item.quantity - 1)}
        onIncrement={() => onQtyChange(item.quantity + 1)}
      />
      <span className="text-sm font-bold text-gray-900 w-10 text-right shrink-0 tabular-nums">₹{lineTotal}</span>
      <button
        onClick={onRemove}
        className="w-6 h-6 rounded-full text-gray-300 flex items-center justify-center text-xs hover:bg-red-50 hover:text-red-400 transition-colors shrink-0"
      >
        ✕
      </button>
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function QuickOrderPage() {
  const router = useRouter()
  const [basket, setBasket]               = useState<QuickBasketItem[]>([])
  const [total, setTotal]                 = useState(0)
  const [placing, setPlacing]             = useState(false)
  const [order, setOrder]                 = useState<QuickOrderResult | null>(null)
  const [error, setError]                 = useState<string | null>(null)
  const [recentOrders, setRecentOrders]   = useState<QuickRecentOrder[]>([])
  const [loadingBasket, setLoadingBasket] = useState(true)
  const [reordering, setReordering]       = useState<string | null>(null)

  // Stable search function for ItemSearchDropdown
  const handleSearch = useCallback(async (q: string): Promise<ProductSearchResult[]> => {
    const res = await api.quick.search(q)
    if (res.success && res.data) return res.data.results
    return []
  }, [])

  useEffect(() => {
    Promise.all([
      api.quick.getBasket(),
      api.quick.recentOrders(),
    ]).then(([basketRes, recentRes]) => {
      if (basketRes.success && basketRes.data) {
        setBasket(basketRes.data.items)
        setTotal(basketRes.data.estimated_total)
      }
      if (recentRes.success && recentRes.data) {
        setRecentOrders(recentRes.data.orders)
      }
      setLoadingBasket(false)
    })
  }, [])

  function recalcTotal(items: QuickBasketItem[]) {
    return items.reduce((s, i) => s + i.unit_price * i.quantity, 0)
  }

  async function handleSelect(product: ProductSearchResult) {
    if (!product.sku_id) return
    const res = await api.quick.addItem({
      item_name:  product.item_name,
      brand:      product.brand,
      sku_id:     product.sku_id,
      spin_id:    product.spin_id,
      unit:       product.unit,
      quantity:   1,
      unit_price: product.unit_price,
      in_stock:   product.in_stock,
    })
    if (res.success && res.data) {
      const updated = [...basket, res.data.item]
      setBasket(updated)
      setTotal(recalcTotal(updated))
    } else {
      setError(res.error?.message ?? "Could not add item.")
    }
  }

  async function updateQty(id: string, qty: number) {
    if (qty < 1) return removeItem(id)
    const res = await api.quick.updateItem(id, { quantity: qty })
    if (res.success && res.data) {
      const updated = basket.map(i => i.id === id ? res.data!.item : i)
      setBasket(updated)
      setTotal(recalcTotal(updated))
    } else {
      setError(res.error?.message ?? "Could not update quantity.")
    }
  }

  async function handleReorder(orderId: string) {
    setReordering(orderId)
    setError(null)
    const res = await api.quick.reorder(orderId)
    setReordering(null)
    if (res.success && res.data) {
      const updated = [...basket, ...res.data.items]
      setBasket(updated)
      setTotal(recalcTotal(updated))
    } else {
      setError(res.error?.message ?? "Could not reorder.")
    }
  }

  async function removeItem(id: string) {
    const prev = basket
    const updated = basket.filter(i => i.id !== id)
    setBasket(updated)
    setTotal(recalcTotal(updated))
    const res = await api.quick.removeItem(id)
    if (!res.success) {
      setBasket(prev)
      setTotal(recalcTotal(prev))
      setError(res.error?.message ?? "Could not remove item.")
    }
  }

  async function placeOrder() {
    setPlacing(true)
    setError(null)
    const res = await api.quick.checkout()
    setPlacing(false)
    if (res.success && res.data) {
      setOrder(res.data)
      setView("confirmed")
    } else {
      setError(res.error?.message ?? "Checkout failed. Please try again.")
    }
  }

  // view state only needed for confirmed screen
  const [view, setView] = useState<"main" | "confirmed">("main")

  // ── Confirmed ────────────────────────────────────────────────────────────────
  if (view === "confirmed" && order) {
    return (
      <AppShell>
        <div className="flex items-center gap-2 mb-5">
          <button onClick={() => router.push("/dashboard")} className="text-[#D8F3DC] text-lg">←</button>
          <h1 className="text-white font-bold text-lg">Order placed</h1>
        </div>

        <Card>
          {/* success header */}
          <div className="bg-[#1B4332] px-5 py-6 text-center">
            <div className="text-4xl mb-3">✅</div>
            <p className="text-white font-bold text-lg">Order on its way!</p>
            <p className="text-white/60 text-xs mt-1">Swiggy ID: {order.swiggy_order_id}</p>
          </div>

          <div className="divide-y divide-gray-50">
            {order.items.map(i => (
              <div key={i.id} className="flex justify-between items-center px-5 py-3 text-sm">
                <div className="min-w-0">
                  <span className="font-medium text-gray-800">{i.item_name}</span>
                  {i.brand && <span className="text-gray-400"> · {i.brand}</span>}
                  <span className="text-gray-400"> ×{i.quantity}</span>
                </div>
                <span className="font-semibold text-gray-800 shrink-0 ml-3 tabular-nums">
                  ₹{Math.round(i.unit_price * i.quantity)}
                </span>
              </div>
            ))}
          </div>

          <div className="px-5 py-4 border-t border-gray-100 space-y-1.5 text-sm">
            <div className="flex justify-between text-gray-500"><span>Items</span><span>₹{Math.round(order.item_total)}</span></div>
            {order.delivery_fee > 0 && <div className="flex justify-between text-gray-500"><span>Delivery</span><span>₹{Math.round(order.delivery_fee)}</span></div>}
            {order.taxes > 0 && <div className="flex justify-between text-gray-500"><span>Taxes</span><span>₹{Math.round(order.taxes)}</span></div>}
            <div className="flex justify-between font-bold text-gray-900 text-base pt-1">
              <span>Total</span>
              <span className="tabular-nums">₹{Math.round(order.grand_total)}</span>
            </div>
          </div>

          <div className="px-5 pb-5">
            <Button onClick={() => router.push("/dashboard")}>Back to home</Button>
          </div>
        </Card>
      </AppShell>
    )
  }

  // ── Main view ────────────────────────────────────────────────────────────────
  const hasItems = basket.length > 0
  const hasOos   = basket.some(i => i.in_stock === false)

  return (
    <AppShell>
      <div className="flex items-center gap-2 mb-5">
        <button onClick={() => router.push("/dashboard")} className="text-[#D8F3DC] text-lg">←</button>
        <h1 className="text-white font-bold text-lg">Quick Order</h1>
      </div>

      {error && <div className="mb-3"><Alert type="error" message={error} /></div>}

      {loadingBasket ? (
        <div className="flex justify-center py-12"><Spinner /></div>
      ) : hasItems ? (
        /* ── Basket card ─────────────────────────────────────────────────────── */
        <>
          <Card>
            {/* Neutral header */}
            <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
              <span className="text-sm font-bold text-gray-900">Your basket</span>
              <span className="text-xs text-gray-400">{basket.length} item{basket.length !== 1 ? "s" : ""}</span>
            </div>

            {/* OOS warning */}
            {hasOos && (
              <div className="px-4 pt-3">
                <Alert type="error" message="Some items are out of stock. Remove them before placing your order." />
              </div>
            )}

            {/* Item rows */}
            <div className="pt-1">
              {basket.map(item => (
                <BasketRow
                  key={item.id}
                  item={item}
                  onQtyChange={(qty) => updateQty(item.id, qty)}
                  onRemove={() => removeItem(item.id)}
                />
              ))}
            </div>

            {/* Add more */}
            <div className="px-4 py-3 border-t border-gray-50">
              <ItemSearchDropdown onSearch={handleSearch} onSelect={handleSelect} />
            </div>

            {/* Total */}
            <div className="px-4 py-3 border-t border-gray-100 flex items-center justify-between">
              <span className="text-sm text-gray-500 font-medium">Estimated total</span>
              <span className="text-lg font-bold text-gray-900 tabular-nums">₹{Math.round(total)}</span>
            </div>
          </Card>

          <div className="mt-4 space-y-2">
            <Button onClick={placeOrder} loading={placing} disabled={hasOos}>
              Place Order · ₹{Math.round(total)}
            </Button>
            <button
              onClick={async () => {
                setBasket([]); setTotal(0)
                await api.quick.clearBasket()
              }}
              className="w-full text-sm font-semibold text-[#D8F3DC]/60 py-2"
            >
              Clear basket
            </button>
          </div>
        </>
      ) : (
        /* ── Empty state ─────────────────────────────────────────────────────── */
        <>
          <Card>
            <div className="px-5 py-8 text-center border-b border-gray-50">
              <div className="text-4xl mb-3">🛒</div>
              <p className="font-bold text-gray-900">Order anything, right now</p>
              <p className="text-sm text-gray-400 mt-2 leading-relaxed">
                Search Swiggy Instamart and build your basket.<br/>No planning needed.
              </p>
            </div>
            <div className="p-4">
              <ItemSearchDropdown onSearch={handleSearch} onSelect={handleSelect} />
            </div>
          </Card>

          {/* Recent quick orders */}
          {recentOrders.length > 0 && (
            <div className="mt-4">
              <p className="text-xs font-bold text-white/30 uppercase tracking-widest mb-2 px-1">Recent orders</p>
              <Card>
                <div className="divide-y divide-gray-50">
                  {recentOrders.map(o => (
                    <div key={o.order_id} className="flex items-center gap-3 px-4 py-3">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-800">
                          {new Date(o.placed_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}
                          <span className="text-gray-400 font-normal"> · {o.item_count} item{o.item_count !== 1 ? "s" : ""}</span>
                        </p>
                        <p className="text-xs text-gray-400 tabular-nums">₹{Math.round(o.grand_total)}</p>
                      </div>
                      <button
                        onClick={() => handleReorder(o.order_id)}
                        disabled={reordering === o.order_id}
                        className="text-xs font-semibold text-[#2D6A4F] border border-[#2D6A4F]/30 rounded-lg px-3 py-1.5 hover:bg-[#D8F3DC]/40 transition-colors disabled:opacity-40"
                      >
                        {reordering === o.order_id ? "…" : "Reorder"}
                      </button>
                    </div>
                  ))}
                </div>
              </Card>
            </div>
          )}
        </>
      )}
    </AppShell>
  )
}
