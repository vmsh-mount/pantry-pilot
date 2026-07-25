/**
 * PantryPilot shared UI primitives.
 * Design tokens: green=#2D6A4F, orange=#F4845F, bg=#F7F8F5, pale-green=#D8F3DC
 */

"use client"

import React, { useRef } from "react"
import { usePathname, useRouter } from "next/navigation"
import Link from "next/link"
import {
  IconHome, IconPantry, IconOrders, IconRoutines, IconSettings,
  IconChevronRight, IconBack,
} from "./icons"

// ── Design tokens ─────────────────────────────────────────────────────────────

export const T = {
  green:     "#2D6A4F",
  greenDark: "#1B4332",
  pale:      "#D8F3DC",
  orange:    "#F4845F",
  bg:        "#F4F4F4",
  card:      "#FFFFFF",
  hairline:  "rgba(0,0,0,0.07)",
  ink:       "#1C1C1E",
  ink2:      "#5A5A5F",
  ink3:      "#8E8E93",
  /** Standard hero/full-green background — same gradient as onboarding's inference step. */
  gradient:  "linear-gradient(135deg, #1B4332, #2D6A4F)",
} as const

// ── Shell ─────────────────────────────────────────────────────────────────────

/** Full-screen green background — for onboarding / auth flows. */
export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="h-dvh flex flex-col items-center overflow-hidden px-4 pt-8 pb-6" style={{ background: T.gradient }}>
      <div className="w-full max-w-[390px] flex flex-col h-full">
        {children}
      </div>
    </main>
  )
}

// ── BottomNav ─────────────────────────────────────────────────────────────────

const NAV_TABS = [
  { href: "/dashboard", label: "Home",     Icon: IconHome },
  { href: "/pantry",    label: "Pantry",   Icon: IconPantry },
  { href: "/orders",    label: "Orders",   Icon: IconOrders },
  { href: "/routines",  label: "Routines", Icon: IconRoutines },
  { href: "/settings",  label: "Settings", Icon: IconSettings },
]

export function BottomNav() {
  const pathname = usePathname()
  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 flex justify-center pointer-events-none">
      <div className="w-full max-w-[390px] pointer-events-auto">
        <div
          className="mx-3 mb-3 bg-white rounded-2xl shadow-lg border border-gray-100 flex"
          style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
        >
          {NAV_TABS.map(({ href, label, Icon }) => {
            const active = pathname === href || pathname.startsWith(href + "/")
            return (
              <Link
                key={href}
                href={href}
                className="flex-1 flex flex-col items-center py-2.5 gap-1"
              >
                <span
                  className="flex items-center justify-center rounded-[10px] px-3 py-[3px] transition-colors"
                  style={{ background: active ? T.pale : "transparent" }}
                >
                  <Icon size={20} color={active ? T.green : T.ink3} />
                </span>
                <span
                  className="text-[9.5px] font-semibold"
                  style={{ color: active ? T.green : T.ink3 }}
                >
                  {label}
                </span>
              </Link>
            )
          })}
        </div>
      </div>
    </nav>
  )
}

// ── PageShell / PageHero ──────────────────────────────────────────────────────

/**
 * Canonical authenticated layout: green hero (only as tall as its content) +
 * light-gray scrollable body + persistent BottomNav. Pass hero content via
 * `hero`; body content as children.
 */
export function PageShell({
  hero,
  children,
  showNav = true,
  heroBg = "gradient",
}: {
  hero: React.ReactNode
  children: React.ReactNode
  showNav?: boolean
  /** Hero background. "gradient" (default) matches onboarding's inference step; "flat" is the plain green fallback. */
  heroBg?: "flat" | "gradient"
}) {
  const hero_bg = heroBg === "gradient" ? T.gradient : T.green
  return (
    <main className="min-h-screen flex flex-col items-center" style={{ background: T.bg }}>
      {/* Green hero */}
      <div className="w-full" style={{ background: hero_bg }}>
        <div className="w-full max-w-[390px] mx-auto px-4 pt-6 pb-6">{hero}</div>
      </div>
      {/* Gray body */}
      <div className="w-full flex-1" style={{ background: T.bg }}>
        <div className="w-full max-w-[390px] mx-auto px-4 pt-4 pb-28">{children}</div>
      </div>
      {showNav && <BottomNav />}
    </main>
  )
}

/**
 * Standard hero content. Two shapes:
 *  - Tab pages: brand bar (🥦 PantryPilot + settings gear) + title + subtitle.
 *    Used by Orders, Settings, Routines.
 *  - Sub pages: a back button + title (+ subtitle), no brand bar. Pass `back`
 *    (a handler, or `true` to use router.back()). Used by Quick, Flow, Nutrition.
 *
 * Data-rich screens (Dashboard, Pantry) supply their own hero via PageShell's
 * `hero` slot and use <HeroBrandBar/> directly.
 */
export function PageHero({
  title,
  subtitle,
  showSettingsGear = true,
  back,
}: {
  title: string
  subtitle?: string
  showSettingsGear?: boolean
  back?: boolean | (() => void)
}) {
  if (back) {
    return (
      <>
        <div className="flex items-center gap-3 mb-0.5">
          <BackButton
            variant="on-green"
            onClick={typeof back === "function" ? back : undefined}
          />
          <h2 className="text-[19px] font-bold text-white tracking-tight">{title}</h2>
        </div>
        {subtitle && (
          <p className="text-[12px] mt-1 ml-[42px]" style={{ color: "rgba(255,255,255,0.6)" }}>
            {subtitle}
          </p>
        )}
      </>
    )
  }
  return (
    <>
      <HeroBrandBar showSettingsGear={showSettingsGear} />
      <h2 className="text-[19px] font-bold text-white tracking-tight">{title}</h2>
      {subtitle && (
        <p className="text-[12px] mt-0.5" style={{ color: "rgba(255,255,255,0.6)" }}>
          {subtitle}
        </p>
      )}
    </>
  )
}

/** The 🥦 PantryPilot brand row with an optional settings gear (top-right). */
export function HeroBrandBar({
  showSettingsGear = true,
}: {
  showSettingsGear?: boolean
}) {
  const router = useRouter()
  return (
    <div className="flex items-center justify-between mb-3.5">
      <div className="flex items-center gap-2">
        <span className="text-xl leading-none">🥦</span>
        <span className="text-[15px] font-bold text-white">PantryPilot</span>
      </div>
      {showSettingsGear && (
        <button onClick={() => router.push("/settings")} aria-label="Settings" style={{ opacity: 0.5 }}>
          <IconSettings size={18} color="white" />
        </button>
      )}
    </div>
  )
}

// ── BackButton ────────────────────────────────────────────────────────────────

/**
 * Single icon-only back control. The page title carries the label — no "Back"
 * word. `variant="on-green"` for placement inside the hero; default for light.
 */
export function BackButton({
  onClick,
  variant = "light",
  className = "",
}: {
  onClick?: () => void
  variant?: "light" | "on-green"
  className?: string
}) {
  const router = useRouter()
  const onGreen = variant === "on-green"
  return (
    <button
      type="button"
      aria-label="Back"
      onClick={onClick ?? (() => router.back())}
      className={`w-[30px] h-[30px] rounded-[10px] flex items-center justify-center shrink-0 ${className}`}
      style={{ background: onGreen ? "rgba(255,255,255,0.16)" : "rgba(0,0,0,0.05)" }}
    >
      <IconBack size={18} color={onGreen ? "white" : T.ink} />
    </button>
  )
}

// ── AppShell (deprecated) ─────────────────────────────────────────────────────

/**
 * @deprecated Migrate to <PageShell/>. Kept only for out-of-scope secondary
 * screens (flow, quick, runs, routine detail/new/edit, nutrition, reauth)
 * until they are moved onto PageShell in a follow-up task. Do not use on new
 * or migrated pages — it renders a full-viewport green background.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-screen bg-[#2D6A4F] flex flex-col items-center justify-start pt-6 pb-28 px-4">
      <div className="w-full max-w-[390px]">{children}</div>
      <BottomNav />
    </main>
  )
}

/** White rounded card. */
export function Card({
  children,
  className = "",
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={`bg-white rounded-3xl shadow-xl overflow-hidden ${className}`}>
      {children}
    </div>
  )
}

export function CardBody({
  children,
  spacing = "space-y-4",
  className = "",
}: {
  children: React.ReactNode
  spacing?: string
  className?: string
}) {
  return (
    <div className={`px-6 pt-5 pb-6 ${spacing} ${className}`}>
      {children}
    </div>
  )
}

// ── StepBar ───────────────────────────────────────────────────────────────────

/** N separate thin bars — filled up to current step. Shown only during questionnaire steps. */
export function StepBar({ step, total }: { step: number; total: number }) {
  return (
    <div className="px-6 pt-5 pb-2 flex gap-1.5">
      {Array.from({ length: total }).map((_, i) => (
        <div
          key={i}
          className={`flex-1 h-1 rounded-full transition-all duration-300 ${
            i < step ? "bg-[#2D6A4F]" : "bg-gray-100"
          }`}
        />
      ))}
    </div>
  )
}

// ── ProgressBar (legacy single-fill) ─────────────────────────────────────────

export function ProgressBar({ step, total }: { step: number; total: number }) {
  const pct = Math.round((step / total) * 100)
  return (
    <div className="px-6 pt-5 pb-1">
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div
          className="h-full bg-[#2D6A4F] rounded-full transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

// ── Buttons ───────────────────────────────────────────────────────────────────

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger"
  loading?: boolean
}

export function Button({
  children,
  variant = "primary",
  loading = false,
  disabled,
  className = "",
  ...props
}: ButtonProps) {
  const base = "w-full py-3.5 rounded-2xl font-semibold text-sm transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
  const variants = {
    primary:   "bg-[#2D6A4F] hover:bg-[#1B4332] active:bg-[#1B4332] text-white",
    secondary: "bg-white hover:bg-[#D8F3DC] active:bg-[#D8F3DC] text-[#2D6A4F] border-[1.5px] border-[#2D6A4F]",
    ghost:     "text-[#2D6A4F] hover:bg-[#D8F3DC]/40",
    danger:    "bg-red-50 hover:bg-red-100 text-red-600 border border-red-200",
  }
  return (
    <button
      className={`${base} ${variants[variant]} ${className}`}
      disabled={loading || disabled}
      {...props}
    >
      {loading ? <Spinner size="sm" /> : null}
      {children}
    </button>
  )
}

// ── Spinner ───────────────────────────────────────────────────────────────────

export function Spinner({ size = "md" }: { size?: "sm" | "md" | "lg" }) {
  const sizes = { sm: "h-4 w-4", md: "h-6 w-6", lg: "h-10 w-10" }
  return (
    <svg
      className={`animate-spin ${sizes[size]} text-current`}
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  )
}

// ── Form inputs ───────────────────────────────────────────────────────────────

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label: string
  error?: string
  hint?: string
}

export function Input({ label, error, hint, className = "", ...props }: InputProps) {
  return (
    <div className="space-y-1">
      <label className="block text-sm font-medium text-gray-700">{label}</label>
      <input
        className={`w-full px-4 py-3 rounded-xl border text-sm transition-colors
          ${error
            ? "border-red-400 focus:ring-red-300 bg-red-50"
            : "border-gray-200 focus:border-[#2D6A4F] focus:ring-[#D8F3DC] bg-white"
          }
          focus:outline-none focus:ring-2 ${className}`}
        {...props}
      />
      {hint  && !error && <p className="text-xs text-gray-400">{hint}</p>}
      {error && <p className="text-xs text-red-500">{error}</p>}
    </div>
  )
}

// ── OptionGrid — 2×2 large emoji option cards ─────────────────────────────────

export function OptionGrid<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string; emoji: string; sub?: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="grid grid-cols-2 gap-3">
      {options.map((opt) => {
        const active = value === opt.value
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={`flex flex-col items-center justify-center py-5 px-3 rounded-2xl border-2 transition-all text-center
              ${active
                ? "border-[#2D6A4F] bg-[#D8F3DC]"
                : "border-gray-100 bg-[#F7F8F5] hover:border-[#2D6A4F]/40"
              }`}
          >
            <span className="text-3xl mb-1.5">{opt.emoji}</span>
            <span className={`text-sm font-semibold ${active ? "text-[#2D6A4F]" : "text-gray-700"}`}>
              {opt.label}
            </span>
            {opt.sub && (
              <span className="text-xs text-gray-400 mt-0.5">{opt.sub}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}

// ── OptionList — full-width radio rows ────────────────────────────────────────

export function OptionList<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string; emoji: string; sub?: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="space-y-2">
      {options.map((opt) => {
        const active = value === opt.value
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={`w-full flex items-center gap-4 px-4 py-3.5 rounded-2xl border-2 transition-all text-left
              ${active
                ? "border-[#2D6A4F] bg-[#D8F3DC]"
                : "border-gray-100 bg-[#F7F8F5] hover:border-[#2D6A4F]/40"
              }`}
          >
            <span className="text-2xl">{opt.emoji}</span>
            <div className="flex-1">
              <p className={`text-sm font-semibold ${active ? "text-[#2D6A4F]" : "text-gray-800"}`}>
                {opt.label}
              </p>
              {opt.sub && <p className="text-xs text-gray-400 mt-0.5">{opt.sub}</p>}
            </div>
            <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 ${
              active ? "border-[#2D6A4F] bg-[#2D6A4F]" : "border-gray-300"
            }`}>
              {active && <div className="w-2 h-2 rounded-full bg-white" />}
            </div>
          </button>
        )
      })}
    </div>
  )
}

// ── BudgetGrid — 2×2 preset budget cards ─────────────────────────────────────

export function BudgetGrid({
  presets,
  value,
  onChange,
}: {
  presets: { min: number; max: number; label: string; sub: string }[]
  value: number   // weekly_budget_max
  onChange: (min: number, max: number) => void
}) {
  return (
    <div className="grid grid-cols-2 gap-3">
      {presets.map((p) => {
        const active = value === p.max
        return (
          <button
            key={p.max}
            type="button"
            onClick={() => onChange(p.min, p.max)}
            className={`flex flex-col items-start px-4 py-4 rounded-2xl border-2 transition-all
              ${active
                ? "border-[#2D6A4F] bg-[#D8F3DC]"
                : "border-gray-100 bg-[#F7F8F5] hover:border-[#2D6A4F]/40"
              }`}
          >
            <span className={`text-base font-bold mb-0.5 ${active ? "text-[#2D6A4F]" : "text-gray-800"}`}>
              {p.label}
            </span>
            <span className="text-xs text-gray-500">{p.sub}</span>
          </button>
        )
      })}
    </div>
  )
}

// ── BudgetBar ─────────────────────────────────────────────────────────────────

export function BudgetBar({
  spent,
  budget,
  label = "Budget usage",
}: {
  spent: number
  budget: number
  label?: string
}) {
  const pct = budget > 0 ? Math.min(100, Math.round((spent / budget) * 100)) : 0
  const over = pct >= 90
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-white/70">{label}</span>
        <span className="text-white font-semibold">
          ₹{Math.round(spent).toLocaleString("en-IN")} of ₹{Math.round(budget).toLocaleString("en-IN")}
        </span>
      </div>
      <div className="h-1.5 bg-white/20 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${over ? "bg-[#F4845F]" : "bg-[#D8F3DC]"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

// ── SubstitutionBanner ────────────────────────────────────────────────────────

export function SubstitutionBanner({ items }: { items: { item_name: string; original_item_name?: string }[] }) {
  if (items.length === 0) return null
  const first = items[0]
  const label = items.length === 1
    ? `${first.original_item_name ?? "1 item"} unavailable → ${first.item_name}`
    : `${items.length} substitutions made`
  return (
    <div className="flex items-start gap-2 px-4 py-3 rounded-xl border bg-[#FFF7ED] border-[#FECBA1]">
      <span className="text-base mt-0.5">⚠️</span>
      <div>
        <p className="text-sm font-semibold text-amber-800">{label}</p>
        <p className="text-xs text-amber-600 mt-0.5">Same brand, same size where possible.</p>
      </div>
    </div>
  )
}

// ── OtpInput — 6 individual digit boxes ──────────────────────────────────────

export function OtpInput({
  value,
  onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  const refs = useRef<(HTMLInputElement | null)[]>([])

  function handleKey(i: number, e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Backspace" && !value[i] && i > 0) {
      refs.current[i - 1]?.focus()
      const arr = value.split("")
      arr[i - 1] = ""
      onChange(arr.join(""))
    }
  }

  function handleChange(i: number, e: React.ChangeEvent<HTMLInputElement>) {
    const char = e.target.value.replace(/\D/g, "").slice(-1)
    const arr = value.padEnd(6, " ").split("")
    arr[i] = char
    const next = arr.join("").replace(/ /g, "")
    onChange(next)
    if (char && i < 5) refs.current[i + 1]?.focus()
  }

  function handlePaste(e: React.ClipboardEvent) {
    const pasted = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, 6)
    if (pasted) {
      onChange(pasted)
      refs.current[Math.min(pasted.length, 5)]?.focus()
      e.preventDefault()
    }
  }

  return (
    <div className="flex gap-2 justify-center" onPaste={handlePaste}>
      {Array.from({ length: 6 }).map((_, i) => (
        <input
          key={i}
          ref={(el) => { refs.current[i] = el }}
          type="text"
          inputMode="numeric"
          maxLength={1}
          value={value[i] ?? ""}
          onChange={(e) => handleChange(i, e)}
          onKeyDown={(e) => handleKey(i, e)}
          className="w-11 h-13 text-center text-xl font-bold border-2 rounded-xl outline-none transition-all
            border-gray-200 focus:border-[#2D6A4F] focus:ring-2 focus:ring-[#D8F3DC]
            bg-[#F7F8F5]"
          style={{ height: "52px" }}
        />
      ))}
    </div>
  )
}

// ── SegmentedSelect (legacy) ──────────────────────────────────────────────────

/** Segmented selector — renders a list of options as pill buttons. */
export function SegmentedSelect<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: { value: T; label: string; icon?: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="space-y-2">
      <label className="block text-sm font-medium text-gray-700">{label}</label>
      <div className="grid grid-cols-2 gap-2">
        {options.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={`py-2.5 px-3 rounded-xl text-sm font-medium border transition-all text-left flex items-center gap-2
              ${value === opt.value
                ? "bg-[#2D6A4F] text-white border-[#2D6A4F] shadow-sm"
                : "bg-white text-gray-700 border-gray-200 hover:border-[#2D6A4F]/40"
              }`}
          >
            {opt.icon && <span>{opt.icon}</span>}
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Alert / banner ────────────────────────────────────────────────────────────

export function Alert({
  type = "error",
  message,
}: {
  type?: "error" | "success" | "info"
  message: string
}) {
  const styles = {
    error:   "bg-red-50 border-red-200 text-red-700",
    success: "bg-[#D8F3DC] border-[#2D6A4F]/30 text-[#1B4332]",
    info:    "bg-blue-50 border-blue-200 text-blue-700",
  }
  const icons = { error: "⚠️", success: "✅", info: "ℹ️" }
  return (
    <div className={`flex items-start gap-2 px-4 py-3 rounded-xl border text-sm ${styles[type]}`}>
      <span>{icons[type]}</span>
      <p>{message}</p>
    </div>
  )
}

// ── Section header ─────────────────────────────────────────────────────────────

export function SectionHeader({
  icon,
  title,
  subtitle,
}: {
  icon: string
  title: string
  subtitle?: string
}) {
  return (
    <div className="px-6 pt-5 pb-2">
      <div className="text-3xl mb-2">{icon}</div>
      <h2 className="text-xl font-bold text-gray-900">{title}</h2>
      {subtitle && <p className="text-sm text-gray-500 mt-1">{subtitle}</p>}
    </div>
  )
}

// ── Basket item row ───────────────────────────────────────────────────────────

export function BasketItemRow({
  name,
  brand,
  quantity,
  unit,
  price,
  isSubstitution,
  originalName,
}: {
  name: string
  brand?: string
  quantity: number
  unit: string
  price: number
  isSubstitution?: boolean
  originalName?: string
}) {
  return (
    <div className="flex items-start justify-between py-3 border-b border-gray-50 last:border-0">
      <div className="flex-1 min-w-0 pr-3">
        <p className="text-sm font-medium text-gray-800 truncate">{name}</p>
        {brand && <p className="text-xs text-gray-400">{brand}</p>}
        {isSubstitution && originalName && (
          <p className="text-xs text-amber-600 mt-0.5">
            ⚠️ Substituted for {originalName}
          </p>
        )}
        <p className="text-xs text-gray-500 mt-0.5">
          {quantity} {unit}
        </p>
      </div>
      <p className="text-sm font-semibold text-gray-800 shrink-0">₹{Math.round(price)}</p>
    </div>
  )
}

// ── Toggle switch ─────────────────────────────────────────────────────────────

export function Toggle({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
  description?: string
}) {
  return (
    <div className="flex items-center justify-between py-3">
      <div>
        <p className="text-sm font-medium text-gray-800">{label}</p>
        {description && <p className="text-xs text-gray-500 mt-0.5">{description}</p>}
      </div>
      <button
        type="button"
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors
          ${checked ? "bg-[#2D6A4F]" : "bg-gray-200"}`}
      >
        <span
          className={`inline-block h-5 w-5 translate-y-0.5 rounded-full bg-white shadow transition-transform
            ${checked ? "translate-x-5" : "translate-x-0.5"}`}
        />
      </button>
    </div>
  )
}
