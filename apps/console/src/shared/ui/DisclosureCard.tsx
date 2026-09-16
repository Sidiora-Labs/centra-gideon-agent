import { useId, useState, type ReactNode } from 'react'
import { ChevronRight, type LucideIcon } from 'lucide-react'
import { fvs } from '../theme/fontWeight'
import { accentChip } from '../theme/accent'

export function DisclosureCard({ icon: Icon, label, subtitle, active, count, countLabel = 'available', children }: {
  icon: LucideIcon
  label: string
  subtitle: ReactNode
  active: boolean
  count?: number
  countLabel?: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const bodyId = `disclosure-card-${useId()}`

  return (
    <div className="mb-2 overflow-hidden rounded-lg bg-surface-container">
      {
}
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls={bodyId}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-high focus-visible:-outline-offset-2">
        <ChevronRight size={14} className="shrink-0 text-on-surface-low transition-transform" style={{ transform: open ? 'rotate(90deg)' : 'none', color: open ? 'var(--color-primary)' : undefined }} />
        <span className="grid size-7 shrink-0 place-items-center rounded-md"
          style={active ? accentChip : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
          <Icon size={14} />
        </span>
        <div className="min-w-0 flex-1">
          <div data-type="label-s" className="text-on-surface" style={fvs(500)}>{label}</div>
          <div data-type="caption" className="mt-0.5 text-on-surface-low">{subtitle}</div>
        </div>
        {count !== undefined && count > 0 && (
          <span data-type="caption" className="shrink-0 rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low tabular-nums">{count} {countLabel}</span>
        )}
      </button>

      {open && (
        <div id={bodyId} className="flex flex-col gap-3 border-t border-outline-variant/30 px-4 pb-4 pt-3">
          {children}
        </div>
      )}
    </div>
  )
}
