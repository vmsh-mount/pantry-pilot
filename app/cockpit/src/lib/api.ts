// Browser calls go to /api/* which Next.js proxies to the pilot service.
// This avoids exposing the internal Docker hostname (pilot:8000) to the browser.
// Server-side (SSR) calls use the direct internal URL so they don't loop through the proxy.
const IS_SERVER = typeof window === "undefined"
const API_BASE = IS_SERVER
  ? (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")
  : "/api"

interface APIResponse<T> {
  success: boolean
  data?: T
  error?: { code: string; message: string; retryable: boolean }
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<APIResponse<T>> {
  const res = await fetch(`${API_BASE}/v1${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  })
  return res.json()
}

export interface RunSummary {
  id:             string
  state:          string
  triggered_at:   string
  completed_at:   string | null
  item_count:     number
  total_price:    number | null
  failure_reason: string | null
  failure_stage:  string | null
  skip_reason:    string | null
}

export interface RunsListResponse {
  runs:           RunSummary[]
  filtered_count: number
  next_run_at:    string | null
  stats: {
    total_runs:       number
    last_order_total: number | null
    avg_order_total:  number | null
  }
}

export interface RunItem {
  item_name:           string
  swiggy_product_name: string | null
  brand:               string | null
  quantity:            number
  unit:                string
  total_price:         number | null
  added_by:            string
  is_substitution:     boolean
  original_item_name:  string | null
}

export interface RunItemsResponse { items: RunItem[]; total_price: number | null }

export interface SettingsResponse {
  diet_type:          string | null
  household_type:     string | null
  member_count:       number | null
  weekly_budget_min:  number | null
  weekly_budget_max:  number | null
  preferred_order_day: string | null
  is_paused:          boolean
  next_run_at:        string | null
}

// ── Shared product search result — used by Flow, Quick Order, and Routines ────
export interface ProductSearchResult {
  sku_id:     string | null
  spin_id:    string
  item_name:  string
  brand:      string | null
  unit:       string
  unit_price: number
  in_stock:   boolean
  image_url:  string | null
}

// Keep alias for backward compat within Quick Order
export type QuickSearchResult = ProductSearchResult
export interface QuickSearchResponse { query: string; results: ProductSearchResult[] }

export interface QuickBasketItem {
  id:         string
  item_name:  string
  brand:      string | null
  sku_id:     string | null
  unit:       string
  quantity:   number
  unit_price: number
  in_stock:   boolean
}
export interface QuickBasketResponse { items: QuickBasketItem[]; estimated_total: number }

export interface QuickAddItem {
  item_name:   string
  brand?:      string | null
  sku_id?:     string | null
  spin_id?:    string
  unit?:       string
  quantity?:   number
  unit_price?: number
  in_stock?:   boolean
}

export interface QuickAddress {
  id:                string
  swiggy_address_id: string
  label:             string | null
  is_default:        boolean
}

export interface QuickRecentOrder {
  order_id:    string
  placed_at:   string
  grand_total: number
  item_count:  number
}

export interface QuickOrderResult {
  order_id:        string
  swiggy_order_id: string
  item_total:      number
  delivery_fee:    number
  taxes:           number
  grand_total:     number
  items:           QuickBasketItem[]
}

// ── API client ────────────────────────────────────────────────────────────────
export const api = {
  auth: {
    initiate: () => request<{ redirect_url: string }>("/auth/initiate", { method: "POST" }),
    logout:   () => request("/auth/logout", { method: "POST" }),
    reauth:   () => request<{ redirect_url: string }>("/auth/reauth", { method: "POST" }),
  },
  onboard: {
    status:       () => request("/onboard/status"),
    infer:        () => request("/onboard/infer"),
    saveProfile:  (body: unknown) => request("/onboard/profile", { method: "POST", body: JSON.stringify(body) }),
    sendOtp:      (body: unknown) => request("/onboard/whatsapp/send-otp", { method: "POST", body: JSON.stringify(body) }),
    verifyOtp:    (body: unknown) => request("/onboard/whatsapp/verify-otp", { method: "POST", body: JSON.stringify(body) }),
    basketPreview:() => request("/onboard/basket-preview"),
    complete:     (body: unknown) => request("/onboard/complete", { method: "POST", body: JSON.stringify(body) }),
  },
  basket: {
    pending:        () => request("/basket/pending"),
    confirm:        () => request("/basket/confirm", { method: "POST" }),
    skip:           () => request("/basket/skip",    { method: "POST" }),
    trigger:        () => request("/basket/trigger", { method: "POST" }),
    removeItem:     (itemId: string) => request(`/basket/item/${itemId}`, { method: "DELETE" }),
    searchItems:    (q: string)      => request<{ results: ProductSearchResult[] }>(`/basket/search?q=${encodeURIComponent(q)}`),
    addItem:        (body: {
      sku_id:     string
      item_name:  string
      unit_price: number
      unit?:      string
      image_url?: string | null
      category?:  string | null
      brand?:     string | null
    }) => request("/basket/item", { method: "POST", body: JSON.stringify(body) }),
    editItem:       (id: string, body: { quantity?: number; brand?: string }) =>
                      request(`/basket/item/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  },
  orders: {
    list: () => request("/orders"),
  },
  runs: {
    list: (params?: { status?: string; limit?: number; offset?: number }) => {
      const query = new URLSearchParams(
        Object.entries(params ?? {})
          .filter(([, v]) => v != null)
          .map(([k, v]) => [k, String(v)])
      )
      return request<RunsListResponse>(`/runs?${query}`)
    },
    getItems: (runId: string) => request<RunItemsResponse>(`/runs/${runId}/items`),
  },
  settings: {
    get:    () => request<SettingsResponse>("/settings"),
    update: (body: unknown) => request("/settings", { method: "PATCH", body: JSON.stringify(body) }),
    pause:  (reason: string) => request("/settings/pause", { method: "POST", body: JSON.stringify({ reason }) }),
    resume: () => request("/settings/resume", { method: "POST" }),
    delete: () => request("/settings/account", { method: "DELETE" }),
  },
  routines: {
    list:     ()                         => request("/routines"),
    create:   (body: unknown)            => request("/routines", { method: "POST", body: JSON.stringify(body) }),
    get:      (id: string)               => request(`/routines/${id}`),
    patch:    (id: string, body: unknown) => request(`/routines/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    delete:   (id: string)               => request(`/routines/${id}`, { method: "DELETE" }),
    pause:    (id: string)               => request(`/routines/${id}/pause`, { method: "POST" }),
    resume:   (id: string)               => request(`/routines/${id}/resume`, { method: "POST" }),
    skipNext: (id: string)               => request(`/routines/${id}/skip-next`, { method: "POST" }),
    runs:     (id: string)               => request(`/routines/${id}/runs`),
  },
  products: {
    search: (q: string) => request<ProductSearchResult[]>(`/products/search?q=${encodeURIComponent(q)}`),
  },
  quick: {
    search:     (q: string, limit = 20) => request<QuickSearchResponse>(`/quick/search?q=${encodeURIComponent(q)}&limit=${limit}`),
    getBasket:  ()                      => request<QuickBasketResponse>("/quick/basket"),
    addItem:    (body: QuickAddItem)    => request<{ item: QuickBasketItem }>("/quick/basket/add", { method: "POST", body: JSON.stringify(body) }),
    updateItem: (id: string, body: { quantity?: number; brand?: string }) =>
                                          request<{ item: QuickBasketItem }>(`/quick/basket/item/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    removeItem: (id: string)            => request<{ removed: boolean }>(`/quick/basket/item/${id}`, { method: "DELETE" }),
    addresses:  ()                      => request<{ addresses: QuickAddress[] }>("/quick/addresses"),
    checkout:     (body?: { swiggy_address_id?: string }) =>
                                          request<QuickOrderResult>("/quick/checkout", { method: "POST", body: JSON.stringify(body ?? {}) }),
    recentOrders:  ()                   => request<{ orders: QuickRecentOrder[] }>("/quick/orders/recent"),
    reorder:       (orderId: string)    => request<{ added: number; items: QuickBasketItem[] }>(`/quick/orders/${orderId}/reorder`, { method: "POST" }),
    clearBasket:   ()                   => request<{ cleared: boolean }>("/quick/basket", { method: "DELETE" }),
  },
}
