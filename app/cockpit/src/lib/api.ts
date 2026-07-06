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

export interface RunItemsResponse {
  items: RunItem[]
}

export interface SettingsResponse {
  dry_run:          boolean
  whatsapp_enabled: boolean
  [key: string]: unknown
}

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
    searchItems:    (q: string)      => request<{ results: import("@/components/basket/ItemSearchDropdown").SearchProduct[] }>(`/basket/search?q=${encodeURIComponent(q)}`),
    addItem:        (body: {
      swiggy_product_id: string
      name:    string
      price:   number
      image_url?: string | null
      category?: string | null
      brand?:    string | null
    }) => request("/basket/item", { method: "POST", body: JSON.stringify(body) }),
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
}
