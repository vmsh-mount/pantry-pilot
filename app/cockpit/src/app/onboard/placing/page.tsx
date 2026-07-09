"use client"
import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { Shell, Card, Spinner } from "@/components/ui"

export default function OnboardPlacing() {
  const router  = useRouter()
  const [dots, setDots] = useState(".")

  // Animate the ellipsis
  useEffect(() => {
    const t = setInterval(() => setDots((d) => d.length >= 3 ? "." : d + "."), 600)
    return () => clearInterval(t)
  }, [])

  // Redirect to done after a moment — actual placement happens async via Celery
  useEffect(() => {
    const t = setTimeout(() => router.push("/onboard/done"), 4000)
    return () => clearTimeout(t)
  }, [])

  return (
    <Shell>
      <Card className="p-10 text-center space-y-5">
        <div className="flex justify-center">
          <Spinner size="lg" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-gray-900">Placing your order{dots}</h2>
          <p className="text-sm text-gray-400 mt-1">
            Building your basket on Swiggy Instamart
          </p>
        </div>
        <p className="text-xs text-gray-300">
          We'll send a WhatsApp confirmation once it's placed
        </p>
      </Card>
    </Shell>
  )
}
