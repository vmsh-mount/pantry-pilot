"use client"
import { useRouter } from "next/navigation"
import { Shell, Card, Button } from "@/components/ui"

export default function OnboardDone() {
  const router = useRouter()
  return (
    <Shell>
      <Card className="p-8 text-center space-y-4">
        <div className="text-5xl">✅</div>
        <h2 className="text-xl font-bold text-gray-900">You're all set!</h2>
        <p className="text-sm text-gray-500">
          Your first basket will be ready on your usual order day.
          We'll send it to WhatsApp for your approval.
        </p>
        <div className="bg-[#D8F3DC] rounded-2xl p-4 text-sm text-[#1B4332]">
          Keep an eye on WhatsApp 📱
        </div>
        <Button onClick={() => router.push("/dashboard")}>
          Go to Dashboard →
        </Button>
        <p className="text-xs text-gray-400">
          You can update your preferences any time in Settings
        </p>
      </Card>
    </Shell>
  )
}
