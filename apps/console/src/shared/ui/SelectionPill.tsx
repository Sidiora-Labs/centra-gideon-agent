import { forwardRef } from 'react'
import type { LucideIcon } from 'lucide-react'

export const SelectionPill = forwardRef<HTMLButtonElement, {
  icon: LucideIcon
  label: string
  x: number
  y: number
  onPress: () => void
}>(function SelectionPill({ icon: Icon, label, x, y, onPress }, ref) {
  return (
    <button ref={ref} type="button"
      onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); onPress() }}
      data-type="body-s"
      className="absolute z-30 -translate-x-1/2 -translate-y-full inline-flex items-center gap-1.5 rounded-pill bg-surface-highest px-3 h-8 text-on-surface shadow-lg ring-1 ring-outline-variant/50 hover:bg-surface-high"
      style={{ left: x, top: y }}>
      <Icon size={13} className="text-primary" /> {label}
    </button>
  )
})

export interface SelectionAction { icon: LucideIcon; label: string; onPress: () => void }

export const SelectionToolbar = forwardRef<HTMLDivElement, {
  actions: SelectionAction[]
  x: number
  y: number
}>(function SelectionToolbar({ actions, x, y }, ref) {
  return (
    <div ref={ref}
      className="absolute z-30 -translate-x-1/2 -translate-y-full inline-flex items-center rounded-pill bg-surface-highest h-8 shadow-lg ring-1 ring-outline-variant/50 overflow-hidden"
      style={{ left: x, top: y }}>
      {actions.map((a, i) => (
        <button key={a.label} type="button"
          onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); a.onPress() }}
          data-type="body-s"
          className={`inline-flex items-center gap-1.5 px-3 h-8 text-on-surface hover:bg-surface-high ${i > 0 ? 'border-l border-outline-variant/50' : ''}`}>
          <a.icon size={13} className="text-primary" /> {a.label}
        </button>
      ))}
    </div>
  )
})
