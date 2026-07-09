"use client"

/**
 * Re-authentication page.
 *
 * Reached via the WhatsApp "Reconnect Swiggy" button deep-link:
 *   https://pantrypilot.in/reauth?hid=<household_id>
 *
 * Flow:
 *  1. Show explanation + CTA
 *  2. On click → POST /auth/reauth → receive redirect_url → bounce to Swiggy OAuth
 *  3. Swiggy bounces back to /auth/callback → backend stores new token
 *  4. User lands back here (or /reauth/done) with success state
 */

import { useEffect, useState } from "react"
import { useSearchParams, useRouter } from "next/navigation"
import { api } from "@/lib/api"
import { Shell, Card, Button, Alert, Spinner } from "@/components/ui"

export default function ReauthPage() {
  const params  = useSearchParams()
  const router  = useRouter()
  const success = params.get("success") === "1"
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState("")

  // If Swiggy callback just completed and redirected here with ?success=1
  if (success) {
    return <ReauthSuccess />
  }

  async function handleReauth() {
    setLoading(true)
    setError("")
    const res = await api.auth.reauth()
    if (res.success && res.data) {
      window.location.href = res.data.redirect_url
    } else {
      setLoading(false)
      setError(res.error?.message ?? "Something went wrong. Please try again.")
    }
  }

  return (
    <Shell>
      <Card>
        {/* Header */}
        <div className="bg-gradient-to-br from-amber-500 to-orange-500 px-6 py-8 text-white text-center">
          <div className="text-4xl mb-3">🔗</div>
          <h1 className="text-xl font-bold">Reconnect your Swiggy account</h1>
          <p className="text-orange-100 text-sm mt-2">
            Your session expired. Reconnecting takes less than a minute.
          </p>
        </div>

        <div className="px-6 py-6 space-y-5">
          {/* Why this is needed */}
          <div className="space-y-3">
            {[
              { icon: "⏱️", text: "Swiggy sessions expire every 5 days for security" },
              { icon: "🔒", text: "We never store your Swiggy password" },
              { icon: "✅", text: "Reconnecting restores your full grocery automation" },
            ].map((i) => (
              <div key={i.text} className="flex items-center gap-3 bg-gray-50 rounded-xl p-3">
                <span className="text-lg">{i.icon}</span>
                <p className="text-sm text-gray-700">{i.text}</p>
              </div>
            ))}
          </div>

          {error && <Alert type="error" message={error} />}

          <Button onClick={handleReauth} loading={loading}>
            🧡 Reconnect Swiggy account
          </Button>

          <p className="text-center text-xs text-gray-400">
            You'll be redirected to Swiggy to sign in securely
          </p>
        </div>
      </Card>
    </Shell>
  )
}

function ReauthSuccess() {
  const router = useRouter()

  useEffect(() => {
    const t = setTimeout(() => router.push("/settings"), 3000)
    return () => clearTimeout(t)
  }, [])

  return (
    <Shell>
      <Card className="p-10 text-center space-y-5">
        <div className="text-5xl">✅</div>
        <h2 className="text-xl font-bold text-gray-900">You're reconnected!</h2>
        <p className="text-sm text-gray-500">
          Your weekly grocery automation will continue as usual.
        </p>
        <div className="flex justify-center">
          <Spinner size="sm" />
        </div>
        <p className="text-xs text-gray-300">Redirecting to settings…</p>
      </Card>
    </Shell>
  )
}
