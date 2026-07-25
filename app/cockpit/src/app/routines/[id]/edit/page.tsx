"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter, useParams } from "next/navigation"
import { api, type ProductSearchResult } from "@/lib/api"
import { PageShell, PageHero, Button, Alert, Spinner } from "@/components/ui"
import { ItemSearchDropdown } from "@/components/basket/ItemSearchDropdown"

// ── Types ─────────────────────────────────────────────────────────────────────

interface SelectedItem {
  item_name: string; quantity: number; unit: string
  sku_id?: string | null; swiggy_product_name?: string
}

// ── Step bar ──────────────────────────────────────────────────────────────────

function StepDots({ step }: { step: number }) {
  return (
    <div className="flex gap-2 justify-center py-3">
      {[1, 2, 3].map(n => (
        <div key={n} className={`w-2 h-2 rounded-full transition-all ${
          n < step ? "bg-[#B7DFC6]" : n === step ? "bg-[#2D6A4F]" : "bg-gray-200"
        }`} />
      ))}
    </div>
  )
}

// ── Step 1: Name + Items ──────────────────────────────────────────────────────

function Step1({
  name, setName, items, setItems, onNext,
}: {
  name: string; setName: (v: string) => void
  items: SelectedItem[]; setItems: (v: SelectedItem[]) => void
  onNext: () => void
}) {
  const handleSearch = useCallback(async (q: string): Promise<ProductSearchResult[]> => {
    const res = await api.products.search(q)
    if (res.success && res.data) return Array.isArray(res.data) ? res.data : []
    return []
  }, [])

  function handleSelect(r: ProductSearchResult) {
    if (items.some(i => i.sku_id === r.sku_id)) return
    setItems([...items, {
      item_name: r.item_name,
      quantity: 1,
      unit: r.unit || "unit",
      sku_id: r.sku_id,
      swiggy_product_name: r.item_name,
    }])
  }

  function removeItem(idx: number) {
    setItems(items.filter((_, i) => i !== idx))
  }

  function updateQty(idx: number, qty: number) {
    setItems(items.map((item, i) => i === idx ? { ...item, quantity: Math.max(1, qty) } : item))
  }

  return (
    <>
      <div className="flex-1 overflow-y-auto px-6 pt-4 pb-2 space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Routine name</label>
          <input
            className="w-full border border-gray-200 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-[#2D6A4F]"
            placeholder="e.g. Morning essentials"
            value={name}
            onChange={e => setName(e.target.value)}
          />
        </div>

        {items.length > 0 && (
          <div>
            <p className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-2">Items</p>
            <div className="space-y-2">
              {items.map((item, idx) => (
                <div key={idx} className="flex items-center gap-2 bg-[#F7FAF8] border border-[#D8F3DC] rounded-xl px-3 py-2">
                  <span className="text-sm text-[#1B4332] font-medium flex-1 truncate">{item.item_name}</span>
                  <div className="flex items-center gap-1">
                    <button
                      className="w-6 h-6 rounded-full bg-white border border-gray-200 text-gray-600 text-sm flex items-center justify-center"
                      onClick={() => updateQty(idx, item.quantity - 1)}
                    >−</button>
                    <span className="text-sm font-semibold w-5 text-center">{item.quantity}</span>
                    <button
                      className="w-6 h-6 rounded-full bg-white border border-gray-200 text-gray-600 text-sm flex items-center justify-center"
                      onClick={() => updateQty(idx, item.quantity + 1)}
                    >+</button>
                  </div>
                  <button
                    className="text-gray-300 hover:text-red-400 text-lg leading-none ml-1"
                    onClick={() => removeItem(idx)}
                  >×</button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div>
          <p className="text-sm font-medium text-gray-700 mb-2">Add items</p>
          <ItemSearchDropdown onSearch={handleSearch} onSelect={handleSelect} />
        </div>
      </div>

      <div className="px-6 pb-6 pt-3 border-t border-gray-100">
        <Button onClick={onNext} disabled={!name.trim() || items.length === 0}>
          Next →
        </Button>
      </div>
    </>
  )
}

// ── Step 2: Schedule ──────────────────────────────────────────────────────────

const FREQ_OPTIONS = [
  { label: "Every day",      type: "every_n_days", value: 1 },
  { label: "Every 2 days",   type: "every_n_days", value: 2 },
  { label: "Every 3 days",   type: "every_n_days", value: 3 },
  { label: "Weekly",         type: "weekly",        value: -1 },
  { label: "Every 2 weeks",  type: "every_n_days", value: 14 },
  { label: "Monthly",        type: "monthly",       value: -1 },
  { label: "Custom",         type: "every_n_days", value: -1 },
]

const DAYS  = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
const TIMES = ["7:00", "8:00", "9:00", "10:00", "12:00", "18:00"]
const DURATIONS = [
  { label: "2 weeks",   preset: "2_weeks" },
  { label: "1 month",   preset: "1_month" },
  { label: "Ongoing",   preset: "ongoing" },
  { label: "Pick date", preset: "pick_date" },
]

function Step2({
  freqType, freqValue, scheduleTime, durationPreset, endDate, weekday, monthDay, customN,
  setFreqType, setFreqValue, setScheduleTime, setDurationPreset, setEndDate, setWeekday, setMonthDay, setCustomN,
  onNext, onBack,
}: {
  freqType: string; freqValue: number; scheduleTime: string
  durationPreset: string; endDate: string; weekday: number; monthDay: number; customN: number
  setFreqType: (v: string) => void; setFreqValue: (v: number) => void
  setScheduleTime: (v: string) => void; setDurationPreset: (v: string) => void
  setEndDate: (v: string) => void; setWeekday: (v: number) => void
  setMonthDay: (v: number) => void; setCustomN: (v: number) => void
  onNext: () => void; onBack: () => void
}) {
  const selectedOptIdx = FREQ_OPTIONS.findIndex(o => {
    if (o.type !== freqType) return false
    if (o.value === -1) return true
    return o.value === freqValue
  })

  function selectFreq(idx: number) {
    const opt = FREQ_OPTIONS[idx]
    setFreqType(opt.type)
    if (opt.value > 0) setFreqValue(opt.value)
    else if (opt.label === "Weekly") setFreqValue(weekday)
    else if (opt.label === "Monthly") setFreqValue(monthDay)
    else setFreqValue(customN)
  }

  const canProceed = scheduleTime && durationPreset &&
    (durationPreset !== "pick_date" || endDate)

  return (
    <>
      <div className="flex-1 overflow-y-auto px-6 pt-4 pb-2 space-y-5">
        <div>
          <p className="text-sm font-medium text-gray-700 mb-2">How often?</p>
          <div className="space-y-1.5">
            {FREQ_OPTIONS.map((opt, idx) => (
              <div key={opt.label}>
                <button
                  className={`w-full flex items-center justify-between px-4 py-3 rounded-xl border text-sm transition-all ${
                    selectedOptIdx === idx
                      ? "border-[#2D6A4F] bg-[#F0F9F4] text-[#1B4332] font-medium"
                      : "border-gray-200 bg-white text-gray-700"
                  }`}
                  onClick={() => selectFreq(idx)}
                >
                  {opt.label}
                  {selectedOptIdx === idx && <span className="text-[#2D6A4F] text-base">✓</span>}
                </button>
                {selectedOptIdx === idx && opt.label === "Weekly" && (
                  <div className="grid grid-cols-7 gap-1 mt-1.5">
                    {DAYS.map((d, i) => (
                      <button key={d} onClick={() => { setWeekday(i); setFreqValue(i) }}
                        className={`py-1.5 text-xs rounded-lg border font-medium ${
                          weekday === i ? "bg-[#2D6A4F] text-white border-[#2D6A4F]" : "border-gray-200 text-gray-600"
                        }`}>
                        {d}
                      </button>
                    ))}
                  </div>
                )}
                {selectedOptIdx === idx && opt.label === "Monthly" && (
                  <div className="mt-1.5">
                    <input type="number" min={1} max={28} value={monthDay}
                      onChange={e => { const v = Math.min(28, Math.max(1, +e.target.value)); setMonthDay(v); setFreqValue(v) }}
                      className="w-full border border-gray-200 rounded-xl px-4 py-2 text-sm focus:outline-none focus:border-[#2D6A4F]"
                      placeholder="Day of month (1–28)"
                    />
                  </div>
                )}
                {selectedOptIdx === idx && opt.label === "Custom" && (
                  <div className="mt-1.5 flex items-center gap-2">
                    <span className="text-sm text-gray-600">Every</span>
                    <input type="number" min={1} max={60} value={customN}
                      onChange={e => { const v = Math.max(1, +e.target.value); setCustomN(v); setFreqValue(v) }}
                      className="w-20 border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-[#2D6A4F]"
                    />
                    <span className="text-sm text-gray-600">days</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div>
          <p className="text-sm font-medium text-gray-700 mb-2">Schedule time</p>
          <div className="grid grid-cols-3 gap-2">
            {TIMES.map(t => {
              const [h] = t.split(":").map(Number)
              const label = h < 12 ? `${h}am` : h === 12 ? "12pm" : `${h - 12}pm`
              return (
                <button key={t}
                  onClick={() => setScheduleTime(t)}
                  className={`py-2.5 rounded-xl text-sm border font-medium transition-all ${
                    scheduleTime === t
                      ? "bg-[#2D6A4F] text-white border-[#2D6A4F]"
                      : "bg-white text-gray-700 border-gray-200"
                  }`}>
                  {label}
                </button>
              )
            })}
          </div>
        </div>

        <div>
          <p className="text-sm font-medium text-gray-700 mb-2">Run until</p>
          <div className="grid grid-cols-2 gap-2">
            {DURATIONS.map(d => (
              <button key={d.preset}
                onClick={() => setDurationPreset(d.preset)}
                className={`py-2.5 rounded-xl text-sm border font-medium transition-all ${
                  durationPreset === d.preset
                    ? "bg-[#2D6A4F] text-white border-[#2D6A4F]"
                    : "bg-white text-gray-700 border-gray-200"
                }`}>
                {d.label}
              </button>
            ))}
          </div>
          {durationPreset === "pick_date" && (
            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
              className="mt-2 w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-[#2D6A4F]"
            />
          )}
        </div>
      </div>

      <div className="px-6 pb-6 pt-3 border-t border-gray-100 flex gap-3">
        <Button variant="ghost" onClick={onBack}>← Back</Button>
        <Button onClick={onNext} disabled={!canProceed}>Next →</Button>
      </div>
    </>
  )
}

// ── Step 3: Review ────────────────────────────────────────────────────────────

function Step3({
  name, items, freqType, freqValue, scheduleTime, durationPreset, endDate,
  onSubmit, onBack, loading,
}: {
  name: string; items: SelectedItem[]; freqType: string; freqValue: number
  scheduleTime: string; durationPreset: string; endDate: string
  onSubmit: () => void; onBack: () => void; loading: boolean
}) {
  function freqLabel() {
    if (freqType === "every_n_days") {
      if (freqValue === 1) return "Every day"
      if (freqValue === 14) return "Every 2 weeks"
      return `Every ${freqValue} days`
    }
    if (freqType === "weekly") return `Every ${["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][freqValue]}`
    return `Monthly (day ${freqValue})`
  }

  function durationLabel() {
    if (durationPreset === "2_weeks") return "2 weeks"
    if (durationPreset === "1_month") return "1 month"
    if (durationPreset === "ongoing") return "Ongoing"
    return endDate ? new Date(endDate).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "—"
  }

  const [h, m] = scheduleTime.split(":").map(Number)
  const timeLabel = h < 12 ? `${h}:${String(m).padStart(2,"0")} am` : h === 12 ? `12:${String(m).padStart(2,"0")} pm` : `${h-12}:${String(m).padStart(2,"0")} pm`

  return (
    <>
      <div className="flex-1 overflow-y-auto px-6 pt-4 pb-2 space-y-4">
        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-xl px-4 py-2.5">
          Changes take effect from the next run.
        </p>

        <div className="bg-white border border-gray-100 rounded-2xl overflow-hidden">
          <div className="px-5 py-4" style={{ background: "linear-gradient(135deg, #1B4332, #2D6A4F)" }}>
            <p className="text-white font-semibold text-base">{name}</p>
            <p className="text-green-200 text-xs mt-0.5">{items.map(i => `${i.item_name} ×${i.quantity}`).join(", ")}</p>
          </div>
          <div className="divide-y divide-gray-50">
            {[
              ["Frequency", freqLabel()],
              ["Schedule time", timeLabel],
              ["Runs until", durationLabel()],
            ].map(([label, val]) => (
              <div key={label} className="flex justify-between px-5 py-3 text-sm">
                <span className="text-gray-500">{label}</span>
                <span className="font-medium text-gray-800">{val}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-[#F7F8F5] border border-[#D8F3DC] rounded-2xl p-4 space-y-1">
          <p className="text-xs font-semibold text-[#2D6A4F] uppercase tracking-wide mb-2">Items</p>
          {items.map((item, i) => (
            <div key={i} className="flex justify-between text-sm">
              <span className="text-gray-700">{item.item_name}</span>
              <span className="text-gray-500">×{item.quantity} {item.unit}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="px-6 pb-6 pt-3 border-t border-gray-100 flex gap-3">
        <Button variant="ghost" onClick={onBack}>← Back</Button>
        <Button onClick={onSubmit} loading={loading}>Save changes</Button>
      </div>
    </>
  )
}

// ── Root ──────────────────────────────────────────────────────────────────────

function istHhmm(scheduleTimeIst: string): string {
  // backend returns e.g. "08:00 IST" or "8:00" — extract HH:MM
  const match = scheduleTimeIst.match(/(\d{1,2}:\d{2})/)
  if (!match) return "8:00"
  const [h, m] = match[1].split(":").map(Number)
  return `${h}:${String(m).padStart(2, "0")}`
}

export default function EditRoutinePage() {
  const router     = useRouter()
  const { id }     = useParams<{ id: string }>()

  const [loadingData, setLoadingData] = useState(true)
  const [step, setStep]     = useState(1)
  const [error, setError]   = useState("")
  const [saving, setSaving] = useState(false)

  // Step 1
  const [name, setName]   = useState("")
  const [items, setItems] = useState<SelectedItem[]>([])

  // Step 2
  const [freqType, setFreqType]             = useState("every_n_days")
  const [freqValue, setFreqValue]           = useState(1)
  const [scheduleTime, setScheduleTime]     = useState("8:00")
  const [durationPreset, setDurationPreset] = useState("ongoing")
  const [endDate, setEndDate]               = useState("")
  const [weekday, setWeekday]               = useState(0)
  const [monthDay, setMonthDay]             = useState(1)
  const [customN, setCustomN]               = useState(2)

  useEffect(() => {
    api.routines.get(id).then(res => {
      setLoadingData(false)
      if (!res.success || !res.data) {
        if (res.error?.code === "NOT_AUTHENTICATED") router.replace("/")
        else setError("Routine not found.")
        return
      }
      const r = res.data as {
        name: string; frequency_type: string; frequency_value: number
        schedule_time_ist: string; end_date: string | null
        items: { item_name: string; quantity: number; unit: string; sku_id?: string | null; swiggy_product_name?: string }[]
      }
      setName(r.name)
      setItems(r.items.map(i => ({
        item_name: i.item_name,
        quantity: Number(i.quantity),
        unit: i.unit,
        sku_id: i.sku_id,
        swiggy_product_name: i.swiggy_product_name,
      })))
      setFreqType(r.frequency_type)
      setFreqValue(r.frequency_value)
      if (r.frequency_type === "weekly") setWeekday(r.frequency_value)
      if (r.frequency_type === "monthly") setMonthDay(r.frequency_value)
      setScheduleTime(istHhmm(r.schedule_time_ist))
      if (r.end_date) {
        setDurationPreset("pick_date")
        setEndDate(r.end_date.slice(0, 10))
      } else {
        setDurationPreset("ongoing")
      }
    })
  }, [id, router])

  async function handleSubmit() {
    setError("")
    setSaving(true)
    const body: Record<string, unknown> = {
      name,
      items: items.map(i => ({
        item_name: i.item_name,
        quantity: i.quantity,
        unit: i.unit,
        sku_id: i.sku_id,
        swiggy_product_name: i.swiggy_product_name,
      })),
      frequency_type: freqType,
      frequency_value: freqValue,
      schedule_time: scheduleTime,
    }
    if (durationPreset === "pick_date") {
      body.end_date = endDate
    } else if (durationPreset !== "ongoing") {
      body.duration_preset = durationPreset
    } else {
      body.end_date = null
    }
    const res = await api.routines.patch(id, body)
    setSaving(false)
    if (res.success) {
      router.push(`/routines/${id}`)
    } else {
      setError(res.error?.message ?? "Could not save changes.")
    }
  }

  if (loadingData) return (
    <PageShell hero={<PageHero title="Edit routine" back={() => router.push(`/routines/${id}`)} />}>
      <div className="flex justify-center py-20" style={{ color: "#8E8E93" }}><Spinner size="lg" /></div>
    </PageShell>
  )

  return (
    <PageShell hero={<PageHero title="Edit routine" back={() => step === 1 ? router.push(`/routines/${id}`) : setStep(step - 1)} />}>
      <div className="bg-white rounded-3xl shadow-xl overflow-hidden flex flex-col min-h-[420px]">
        <StepDots step={step} />

        {error && (
          <div className="px-6 pb-2"><Alert type="error" message={error} /></div>
        )}

        {step === 1 && (
          <Step1
            name={name} setName={setName}
            items={items} setItems={setItems}
            onNext={() => setStep(2)}
          />
        )}

        {step === 2 && (
          <Step2
            freqType={freqType} freqValue={freqValue} scheduleTime={scheduleTime}
            durationPreset={durationPreset} endDate={endDate}
            weekday={weekday} monthDay={monthDay} customN={customN}
            setFreqType={setFreqType} setFreqValue={setFreqValue}
            setScheduleTime={setScheduleTime} setDurationPreset={setDurationPreset}
            setEndDate={setEndDate} setWeekday={setWeekday}
            setMonthDay={setMonthDay} setCustomN={setCustomN}
            onNext={() => setStep(3)} onBack={() => setStep(1)}
          />
        )}

        {step === 3 && (
          <Step3
            name={name} items={items} freqType={freqType} freqValue={freqValue}
            scheduleTime={scheduleTime} durationPreset={durationPreset} endDate={endDate}
            onSubmit={handleSubmit} onBack={() => setStep(2)} loading={saving}
          />
        )}
      </div>
    </PageShell>
  )
}
