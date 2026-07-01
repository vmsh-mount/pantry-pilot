"use client"
import { useEffect } from "react"
import { useSearchParams, useRouter } from "next/navigation"

export default function AuthCallback() {
  const params = useSearchParams()
  const router = useRouter()

  useEffect(() => {
    // The backend handles the actual callback and redirects here
    // This page shows a loading state during the redirect
    const error = params.get("error")
    if (error) {
      router.push(`/?error=${error}`)
    }
  }, [params, router])

  return (
    <div className="min-h-screen bg-green-900 flex items-center justify-center">
      <div className="text-center text-white">
        <div className="text-5xl mb-4 animate-pulse">🥦</div>
        <p className="text-lg font-semibold">Connecting your Swiggy account...</p>
        <p className="text-green-300 text-sm mt-2">This takes just a moment</p>
      </div>
    </div>
  )
}
