"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { api } from "@/lib/api"
import { PageShell, PageHero, Card, Spinner, Alert, Button } from "@/components/ui"
import { NutritionCard } from "@/components/nutrition/NutritionCard"

interface Order {
  order_id:              string
  placed_at:             string | null
  total:                 number
  item_count:            number
  preview_items:         string[]
  via_pantrypilot:       boolean
  pantrypilot_order_id:  string | null
}

function fmtDate(iso: string | null) {
  if (!iso) return "—"
  return new Date(iso).toLocaleDateString("en-IN", {
    weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit",
  })
}

export default function OrdersPage() {
  const router = useRouter()
  const [orders,       setOrders]       = useState<Order[]>([])
  const [loading,      setLoading]      = useState(true)
  const [error,        setError]        = useState("")
  const [expandedId,   setExpandedId]   = useState<string | null>(null)

  useEffect(() => {
    api.orders.list().then((res) => {
      setLoading(false)
      if (res.success && res.data) {
        const d = res.data as { orders: Order[] }
        setOrders(d.orders ?? [])
      } else if ((res.error as { code?: string })?.code === "NOT_AUTHENTICATED") {
        router.push("/")
      } else {
        setError((res.error as { message?: string })?.message ?? "Could not load orders.")
      }
    })
  }, [])

  const orderTotal = orders.reduce((sum, o) => sum + (o.total || 0), 0)
  const subtitle = orders.length > 0
    ? `${orders.length} order${orders.length === 1 ? "" : "s"} · ₹${Math.round(orderTotal).toLocaleString("en-IN")}`
    : undefined

  return (
    <PageShell hero={<PageHero title="Order History" subtitle={subtitle} />}>
      {loading ? (
        <div className="flex justify-center py-20" style={{ color: "#8E8E93" }}>
          <Spinner size="lg" />
        </div>
      ) : error ? (
        <div className="space-y-4">
          <Alert type="error" message={error} />
          <Button variant="secondary" onClick={() => router.push("/")}>
            Sign in again
          </Button>
        </div>
      ) : orders.length === 0 ? (
        <Card>
          <div className="px-6 py-12 text-center space-y-3">
            <div className="text-5xl">📦</div>
            <h2 className="text-lg font-bold text-gray-900">No orders yet</h2>
            <p className="text-sm text-gray-500">
              Your first basket will appear here once it's placed.
            </p>
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          {orders.map((order) => (
            <div
              key={order.order_id}
              className="bg-white rounded-[14px] overflow-hidden"
              style={{ border: "1px solid rgba(0,0,0,0.07)" }}
            >
              <div className="px-4 py-4 space-y-2">
                {/* Header row */}
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold text-gray-900">
                      {fmtDate(order.placed_at)}
                    </p>
                    <p className="text-xs text-gray-400 mt-0.5">
                      ₹{Math.round(order.total).toLocaleString("en-IN")} · {order.item_count} items
                    </p>
                  </div>
                  {order.via_pantrypilot && (
                    <span className="shrink-0 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-[#D8F3DC] text-[#2D6A4F]">
                      PantryPilot
                    </span>
                  )}
                </div>

                {/* Item preview tags */}
                {order.preview_items.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {order.preview_items.map((name, i) => (
                      <span
                        key={i}
                        className="px-2 py-0.5 rounded-full text-xs bg-[#F7F8F5] text-gray-600 border border-gray-100"
                      >
                        {name}
                      </span>
                    ))}
                    {order.item_count > 3 && (
                      <span className="px-2 py-0.5 rounded-full text-xs bg-[#F7F8F5] text-gray-400 border border-gray-100">
                        +{order.item_count - 3} more
                      </span>
                    )}
                  </div>
                )}

                {/* Nutrition toggle — only for PantryPilot orders with an internal UUID */}
                {order.pantrypilot_order_id && (
                  <>
                    <div className="pt-1 border-t border-gray-50 flex">
                      <button
                        onClick={() => setExpandedId(expandedId === order.order_id ? null : order.order_id)}
                        className="text-xs text-[#2D6A4F] font-medium flex items-center gap-1 hover:opacity-70 transition-opacity"
                      >
                        <span>🌿</span>
                        <span>{expandedId === order.order_id ? "Hide nutrition" : "Nutrition"}</span>
                      </button>
                    </div>

                    {expandedId === order.order_id && (
                      <div className="pt-1">
                        <NutritionCard orderId={order.pantrypilot_order_id} />
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </PageShell>
  )
}
