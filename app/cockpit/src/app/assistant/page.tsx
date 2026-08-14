"use client"

/**
 * AI ordering assistant — in-app chat.
 * tasks/features/ai-ordering-assistant.md, Design §4.
 *
 * Two render modes for an assistant turn:
 *   - plain text -> a chat bubble
 *   - a proposed write-tool call -> a distinct preview card (not a bubble)
 *     with Confirm/Cancel. Cancel is purely local — it has no backend
 *     counterpart (Design §4); nothing is "confirmed" unless the Confirm
 *     button is pressed, which calls POST /message with confirm_tool_call_id.
 */

import { useEffect, useRef, useState } from "react"
import { api, type AssistantHistoryTurn, type AssistantResponse } from "@/lib/api"
import { PageShell, PageHero, Button, Spinner } from "@/components/ui"

interface ChatBubble {
  role: "user" | "assistant"
  content: string
}

interface PendingProposal {
  tool_call_id: string
  tool_name:    string
  tool_input:   Record<string, unknown>
  preview:      string
}

// Reconstructs a clean chat view + any still-unconfirmed proposal from the
// raw turn history — tool_use/tool_result turns are internal plumbing, not
// rendered as bubbles themselves. See the module comment above.
function buildChatView(turns: AssistantHistoryTurn[]): { bubbles: ChatBubble[]; pending: PendingProposal | null } {
  const confirmedIds = new Set(
    turns
      .filter((t) => t.role === "tool_result" && t.tool_calls && "tool_use_id" in t.tool_calls)
      .map((t) => (t.tool_calls as { tool_use_id: string }).tool_use_id)
  )

  const bubbles: ChatBubble[] = []
  let pending: PendingProposal | null = null

  for (const t of turns) {
    if (t.role === "user") {
      bubbles.push({ role: "user", content: t.content })
    } else if (t.role === "assistant") {
      if (t.tool_calls && "id" in t.tool_calls) {
        const call = t.tool_calls as { id: string; name: string; input: Record<string, unknown>; preview?: string }
        pending = confirmedIds.has(call.id)
          ? null
          : { tool_call_id: call.id, tool_name: call.name, tool_input: call.input, preview: call.preview ?? `${call.name}(...)` }
      } else if (t.content) {
        bubbles.push({ role: "assistant", content: t.content })
      }
    }
    // tool_result turns: internal only, never rendered directly.
  }

  return { bubbles, pending }
}

function Bubble({ bubble }: { bubble: ChatBubble }) {
  const isUser = bubble.role === "user"
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-3`}>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-[13.5px] leading-relaxed ${
          isUser ? "bg-[#2D6A4F] text-white" : "bg-white text-gray-800 border border-gray-100"
        }`}
      >
        {bubble.content}
      </div>
    </div>
  )
}

function ProposalCard({
  proposal, busy, onConfirm, onCancel,
}: {
  proposal: PendingProposal
  busy:     boolean
  onConfirm: () => void
  onCancel:  () => void
}) {
  const isCheckout = proposal.tool_name === "checkout_basket"
  const isDelete   = proposal.tool_name === "delete_routine"
  return (
    <div className="bg-[#FFF8E8] border border-[#F0D98C] rounded-2xl p-4 mb-3">
      <p className="text-[11px] font-bold uppercase tracking-wide text-[#8A6A10] mb-1.5">
        {isCheckout ? "Confirm order" : isDelete ? "Confirm deletion" : "Confirm action"}
      </p>
      <p className="text-[13.5px] text-gray-800 mb-3">{proposal.preview}</p>
      <div className="flex gap-2">
        <Button variant="secondary" onClick={onCancel} disabled={busy} className="!py-2 !text-[13px]">
          Cancel
        </Button>
        <Button
          variant={isDelete ? "danger" : "primary"}
          onClick={onConfirm}
          loading={busy}
          disabled={busy}
          className="!py-2 !text-[13px]"
        >
          {isCheckout ? "Place order" : "Confirm"}
        </Button>
      </div>
    </div>
  )
}

export default function AssistantPage() {
  const [bubbles, setBubbles] = useState<ChatBubble[]>([])
  const [pending, setPending] = useState<PendingProposal | null>(null)
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await api.assistant.history()
        if (cancelled) return
        if (res.success && res.data) {
          const { bubbles, pending } = buildChatView(res.data)
          setBubbles(bubbles)
          setPending(pending)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [bubbles, pending])

  function applyResponse(data: AssistantResponse) {
    if (data.type === "text") {
      setBubbles((prev) => [...prev, { role: "assistant", content: data.message }])
      setPending(null)
    } else {
      setPending({
        tool_call_id: data.tool_call_id, tool_name: data.tool_name,
        tool_input: data.tool_input, preview: data.preview,
      })
    }
  }

  async function handleSend() {
    const message = input.trim()
    if (!message || sending || pending) return
    setError(null)
    setInput("")
    setBubbles((prev) => [...prev, { role: "user", content: message }])
    setSending(true)
    try {
      const res = await api.assistant.send(message)
      if (res.success && res.data) {
        applyResponse(res.data)
      } else {
        setError(res.error?.message ?? "Something went wrong.")
      }
    } catch {
      setError("Couldn't reach the assistant. Try again.")
    } finally {
      setSending(false)
    }
  }

  async function handleConfirm() {
    if (!pending) return
    setConfirming(true)
    setError(null)
    try {
      const res = await api.assistant.confirm(pending.tool_call_id)
      if (res.success && res.data) {
        applyResponse(res.data)
      } else {
        setError(res.error?.message ?? "Couldn't complete that action.")
      }
    } catch {
      setError("Couldn't reach the assistant. Try again.")
    } finally {
      setConfirming(false)
    }
  }

  function handleCancel() {
    // Purely local — no backend call. The proposal just stays un-confirmed
    // in history; nothing was ever executed. See Design §4.
    setPending(null)
    setBubbles((prev) => [...prev, { role: "assistant", content: "No problem — cancelled." }])
  }

  return (
    <PageShell hero={<PageHero title="Assistant" subtitle="Ask about nutrition, routines, or your basket" back />}>
      <div className="flex flex-col" style={{ minHeight: "60vh" }}>
        <div className="flex-1 overflow-y-auto px-1 pb-3">
          {loading ? (
            <div className="flex justify-center py-10"><Spinner /></div>
          ) : bubbles.length === 0 && !pending ? (
            <div className="text-center py-10 px-4">
              <p className="text-[13px] text-gray-400">
                Try &quot;what&apos;s my protein status this week&quot; or &quot;add milk to my basket&quot;.
              </p>
            </div>
          ) : (
            bubbles.map((b, i) => <Bubble key={i} bubble={b} />)
          )}

          {pending && (
            <ProposalCard
              proposal={pending}
              busy={confirming}
              onConfirm={handleConfirm}
              onCancel={handleCancel}
            />
          )}

          {sending && (
            <div className="flex justify-start mb-3">
              <div className="bg-white border border-gray-100 rounded-2xl px-4 py-2.5">
                <Spinner size="sm" />
              </div>
            </div>
          )}

          {error && (
            <p className="text-[12px] text-red-600 text-center mb-2">{error}</p>
          )}

          <div ref={bottomRef} />
        </div>

        <div className="flex items-center gap-2 pt-2 border-t border-gray-100">
          <input
            className="flex-1 rounded-2xl border border-gray-200 px-4 py-3 text-[13.5px] focus:outline-none focus:border-[#2D6A4F]"
            placeholder={pending ? "Confirm or cancel above to continue…" : "Message the assistant…"}
            value={input}
            disabled={sending || !!pending}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleSend() }}
          />
          <button
            onClick={handleSend}
            disabled={sending || !input.trim() || !!pending}
            className="shrink-0 w-11 h-11 rounded-2xl bg-[#2D6A4F] text-white flex items-center justify-center disabled:opacity-40"
            aria-label="Send"
          >
            ➤
          </button>
        </div>
      </div>
    </PageShell>
  )
}
