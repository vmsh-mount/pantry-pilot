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
    pending:  () => request("/basket/pending"),
    confirm:  () => request("/basket/confirm", { method: "POST" }),
    skip:     () => request("/basket/skip",    { method: "POST" }),
    trigger:  () => request("/basket/trigger", { method: "POST" }),
  },
  orders: {
    list: () => request("/orders"),
  },
  settings: {
    get:    () => request("/settings"),
    update: (body: unknown) => request("/settings", { method: "PATCH", body: JSON.stringify(body) }),
    pause:  (reason: string) => request("/settings/pause", { method: "POST", body: JSON.stringify({ reason }) }),
    resume: () => request("/settings/resume", { method: "POST" }),
    delete: () => request("/settings/account", { method: "DELETE" }),
  },
}
