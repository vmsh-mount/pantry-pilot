"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { api } from "@/lib/api"
import { AppShell, Card, Spinner, Alert, Button } from "@/components/ui"

interface Order {
  order_id:        string
  placed_at:       string | null
  total:           number
  item_count:      number
  preview_items:   string[]
  via_pantrypilot: boolean
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
  const [orders,  setOrders]  = useState<Order[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState("")

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

  return (
    <AppShell>
      <div className="px-1 mb-4">
        <h1 className="text-white font-bold text-lg">Order History</h1>
      </div>

      {loading ? (
        <div className="flex justify-center py-20 text-white">
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
            <Card key={order.order_id}>
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
              </div>
            </Card>
          ))}
        </div>
      )}
    </AppShell>
  )
}
