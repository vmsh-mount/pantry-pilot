"use client"

interface Props {
  id:         string
  item_name:  string
  brand?:     string | null
  unit:       string
  unit_price: number
  quantity:   number
  /** Show qty stepper (+/−). When false, only a remove ✕ button is shown. */
  showStepper?: boolean
  removing?:  boolean
  onRemove:   (id: string) => void
  onQtyChange?: (id: string, qty: number) => void
}

export function BasketItemRow({
  id, item_name, brand, unit, unit_price, quantity,
  showStepper = true, removing = false, onRemove, onQtyChange,
}: Props) {
  const total = unit_price * quantity

  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-50 last:border-0">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-900 leading-snug truncate">{item_name}</p>
        {brand && <p className="text-xs text-gray-400 mt-0.5">{brand}</p>}
        <p className="text-xs text-[#2D6A4F] mt-0.5">₹{unit_price} / {unit}</p>
      </div>

      {showStepper && onQtyChange ? (
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={() => onQtyChange(id, quantity - 1)}
            className="w-7 h-7 rounded-full bg-gray-100 text-gray-600 font-bold text-base flex items-center justify-center"
          >−</button>
          <span className="w-6 text-center text-sm font-semibold text-gray-900">{quantity}</span>
          <button
            onClick={() => onQtyChange(id, quantity + 1)}
            className="w-7 h-7 rounded-full bg-[#2D6A4F] text-white font-bold text-base flex items-center justify-center"
          >+</button>
        </div>
      ) : (
        <span className="text-xs text-gray-400 shrink-0">{quantity} {unit}</span>
      )}

      <div className="text-right shrink-0 min-w-[3rem]">
        <p className="text-sm font-semibold text-gray-900">₹{Math.round(total)}</p>
      </div>

      <button
        onClick={() => onRemove(id)}
        disabled={removing}
        className="w-6 h-6 flex items-center justify-center rounded-full text-gray-300 hover:text-red-400 hover:bg-red-50 transition-colors disabled:opacity-30 shrink-0"
        aria-label={`Remove ${item_name}`}
      >
        {removing ? (
          <span className="w-3 h-3 border border-gray-300 border-t-transparent rounded-full animate-spin" />
        ) : (
          <span className="text-xs leading-none">✕</span>
        )}
      </button>
    </div>
  )
}
