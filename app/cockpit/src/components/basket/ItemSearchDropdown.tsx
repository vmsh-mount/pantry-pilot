"use client"

import { useEffect, useRef, useState } from "react"
import type { ProductSearchResult } from "@/lib/api"

// Keep the old name exported so any existing import still compiles.
export type SearchProduct = ProductSearchResult

interface Props {
  onSelect:  (product: ProductSearchResult) => void
  onSearch:  (q: string) => Promise<ProductSearchResult[]>
  disabled?: boolean
}

export function ItemSearchDropdown({ onSelect, onSearch, disabled }: Props) {
  const [open,    setOpen]    = useState(false)
  const [query,   setQuery]   = useState("")
  const [results, setResults] = useState<ProductSearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!query || query.trim().length < 2) {
      setResults([])
      setLoading(false)
      return
    }
    setLoading(true)
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(async () => {
      try {
        const res = await onSearch(query.trim())
        setResults(res)
      } catch {
        setResults([])
      } finally {
        setLoading(false)
      }
    }, 300)
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [query, onSearch])

  function handleSelect(product: ProductSearchResult) {
    onSelect(product)
    setQuery("")
    setResults([])
    setOpen(false)
  }

  if (!open) {
    return (
      <button
        onClick={() => { setOpen(true); setTimeout(() => inputRef.current?.focus(), 50) }}
        disabled={disabled}
        className="w-full flex items-center justify-center gap-2 px-4 py-3 border-2 border-dashed border-[#2D6A4F]/30 rounded-xl text-sm font-medium text-[#2D6A4F] hover:border-[#2D6A4F]/60 hover:bg-[#D8F3DC]/30 transition-colors disabled:opacity-40"
      >
        <span>+</span>
        <span>Add item</span>
      </button>
    )
  }

  return (
    <div className="rounded-xl border border-[#2D6A4F]/30 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-gray-100">
        <span className="text-gray-400 text-sm">🔍</span>
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search for an item…"
          className="flex-1 text-sm text-gray-900 placeholder-gray-400 bg-transparent outline-none"
        />
        <button
          onClick={() => { setOpen(false); setQuery(""); setResults([]) }}
          className="text-gray-400 hover:text-gray-600 text-xs px-1"
        >
          ✕
        </button>
      </div>

      {loading && (
        <div className="px-4 py-3 text-xs text-gray-400">Searching…</div>
      )}

      {!loading && query.trim().length >= 2 && results.length === 0 && (
        <div className="px-4 py-3 text-xs text-gray-400">No results found</div>
      )}

      {!loading && results.length > 0 && (
        <ul className="divide-y divide-gray-50 max-h-56 overflow-y-auto">
          {results.map((p, idx) => (
            <li key={p.sku_id ?? idx}>
              <button
                onClick={() => handleSelect(p)}
                disabled={!p.in_stock}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-[#D8F3DC]/40 transition-colors disabled:opacity-40"
              >
                {p.image_url ? (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img src={p.image_url} alt={p.item_name} className="w-9 h-9 rounded-lg object-cover shrink-0" />
                ) : (
                  <div className="w-9 h-9 rounded-lg bg-gray-100 flex items-center justify-center text-lg shrink-0">🛒</div>
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-900 truncate">{p.item_name}</p>
                  {p.brand && <p className="text-xs text-gray-400 truncate">{p.brand}</p>}
                  {!p.in_stock && <p className="text-xs text-red-400">Out of stock</p>}
                </div>
                <div className="text-right shrink-0">
                  <p className="text-sm font-semibold text-gray-900">₹{Math.round(p.unit_price)}</p>
                  <p className="text-xs text-gray-400">{p.unit}</p>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
