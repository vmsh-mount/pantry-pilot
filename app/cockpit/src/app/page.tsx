"use client"
import { useEffect } from "react"
import { useRouter } from "next/navigation"
import { api } from "@/lib/api"

export default function Landing() {
  const router = useRouter()

  // If already authenticated, skip straight to dashboard
  useEffect(() => {
    api.basket.pending().then((res) => {
      if (res.success) {
        router.replace("/dashboard")
      } else {
        const code = (res.error as { code?: string })?.code
        if (code === "ONBOARDING_INCOMPLETE") {
          router.replace("/onboard")
        }
        // NOT_AUTHENTICATED or any other error → stay on landing
      }
    })
  }, [])

  async function handleConnect() {
    const res = await api.auth.initiate()
    if (res.success && res.data) {
      window.location.href = (res.data as { redirect_url: string }).redirect_url
    }
  }

  return (
    <main className="min-h-screen bg-[#2D6A4F] flex flex-col items-center justify-center p-6">
      <div className="w-full max-w-[390px] bg-white rounded-3xl shadow-2xl overflow-hidden">
        {/* Hero */}
        <div className="bg-[#2D6A4F] px-8 py-10 text-center">
          <div className="inline-flex items-center gap-2 mb-6">
            <span className="text-4xl">🥦</span>
            <span className="text-white text-2xl font-bold">PantryPilot</span>
          </div>
          <h1 className="text-white text-3xl font-extrabold leading-tight mb-3">
            Your weekly groceries,<br />planned for you.
          </h1>
          <p className="text-[#D8F3DC] text-sm">
            Nutrition-aware. Budget-smart.<br />Delivered on Swiggy Instamart.
          </p>
        </div>

        {/* Trust signals */}
        <div className="p-6 space-y-3">
          {[
            { icon: "🛡️", title: "No auto-orders without approval", sub: "You confirm every basket before it's placed" },
            { icon: "🧠", title: "Learns your household over time",  sub: "Gets smarter with every order" },
            { icon: "⚡", title: "Powered by Swiggy Instamart",      sub: "50,000+ SKUs, delivered in minutes" },
          ].map((t) => (
            <div key={t.title} className="flex items-start gap-3 bg-[#F7F8F5] rounded-xl p-3">
              <span className="text-xl">{t.icon}</span>
              <div>
                <p className="text-sm font-semibold text-gray-800">{t.title}</p>
                <p className="text-xs text-gray-500 mt-0.5">{t.sub}</p>
              </div>
            </div>
          ))}

          <button
            onClick={handleConnect}
            className="w-full py-4 bg-[#F4845F] hover:bg-[#e8734e] text-white font-bold rounded-2xl transition-colors flex items-center justify-center gap-2 mt-2"
          >
            🧡 Connect your Swiggy account
          </button>
          <p className="text-center text-xs text-gray-400">
            You'll be redirected to <span className="text-[#F4845F] font-semibold">Swiggy</span> to sign in securely
          </p>
        </div>
      </div>
    </main>
  )
}
