import { useId, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, Plus, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { spring, physics } from '../../shared/theme/motion'
import { fvs } from '../../shared/theme/fontWeight'
import { Toggle } from '../../shared/ui/Toggle'
import { Surface } from '../../shared/ui/Surface'
import { FieldHintProvider, FieldLabelProvider, NumberField } from '../../shared/ui/forms'


export function RowGroup({ children }: { children: ReactNode }) {
  return <Surface tone="container" radius="lg" className="px-l py-xs">{children}</Surface>
}

export function PanelHeader({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mb-l">
      <h1 className="text-on-surface" data-type="title-l">{title}</h1>
      {hint && <p data-type="body-s" className="mt-1 text-on-surface-low">{hint}</p>}
    </div>
  )
}

export function Section({ title, hint, icon: Icon, iconTone = 'primary', right, children }: {
  title?: ReactNode
  hint?: ReactNode
  icon?: LucideIcon
  iconTone?: 'primary' | 'muted'
  right?: ReactNode
  children: ReactNode
}) {
  const heading = title && (
    <h2 data-type="title-m" className={`mb-s text-on-surface${Icon ? ' flex items-center gap-s' : ''}`} style={fvs(600)}>
      {Icon && <Icon size={16} className={`shrink-0 ${iconTone === 'muted' ? 'text-on-surface-low' : 'text-primary'}`} />}
      {title}
    </h2>
  )
  const hintEl = hint && <p className="mb-m text-on-surface-low text-[0.8125rem]">{hint}</p>
  return (
    <section className="mb-2xl">
      {right ? (
        <div className="flex items-start justify-between gap-s">
          <div className="min-w-0">{heading}{hintEl}</div>
          <div className="shrink-0">{right}</div>
        </div>
      ) : (<>{heading}{hintEl}</>)}
      {children}
    </section>
  )
}

export function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  const hintId = useId()
  return (
    <FieldHintProvider value={hint ? hintId : undefined}>
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-l border-b border-outline-variant/30 py-3 last:border-0">
        <div data-type="body-s" className="text-on-surface">{label}</div>
        {hint && <div id={hintId} data-type="body-s" className="mt-0.5 text-on-surface-low">{hint}</div>}
        {
}
        <div className="col-start-2 row-start-1 flex items-center">{children}</div>
      </div>
    </FieldHintProvider>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  const labelId = useId()
  const hintId = useId()
  return (
    <FieldLabelProvider value={labelId}>
      <FieldHintProvider value={hint ? hintId : undefined}>
        <div className="border-b border-outline-variant/30 py-3 last:border-0">
          <div id={labelId} data-type="body-s" className="text-on-surface">{label}</div>
          {hint && <div id={hintId} data-type="body-s" className="mt-0.5 mb-2 text-on-surface-low">{hint}</div>}
          <div className="mt-2">{children}</div>
        </div>
      </FieldHintProvider>
    </FieldLabelProvider>
  )
}

export { Toggle } from '../../shared/ui/Toggle'

export function SegPills<T extends string>({ value, onChange, options, ariaLabel }: {
  value: T; onChange: (v: T) => void; options: { key: T; label: string }[]; ariaLabel: string
}) {
  const indicatorId = `segpills-${useId()}`
  return (
    <div className="inline-flex rounded-pill bg-surface-container p-0.5">
      {options.map((o) => {
        const on = o.key === value
        return (
          <button key={o.key} type="button" onClick={() => onChange(o.key)}
            aria-label={`${ariaLabel}: ${o.label}`} aria-pressed={on}
            data-type="body-s" className="relative rounded-pill px-3 h-7 transition-colors"
            style={{ color: on ? 'var(--color-on-surface)' : 'var(--color-on-surface-low)' }}>
            {
}
            {on && <motion.span layoutId={indicatorId} transition={spring.spatialFast}
              className="absolute inset-0 rounded-pill" style={{ background: 'var(--color-surface-highest)' }} />}
            <span className="relative">{o.label}</span>
          </button>
        )
      })}
    </div>
  )
}

export function SavedToast({ show }: { show: boolean }) {
  return (
    <>
      <span role="status" aria-live="polite" className="sr-only">{show ? 'Saved' : ''}</span>
      <AnimatePresence>
        {show && (
          <motion.span aria-hidden="true" initial={{ opacity: 0, scale: 0.8, y: 2 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.8 }}
            transition={physics.playful} data-type="caption" style={{ color: 'var(--color-success)' }}>Saved ✓</motion.span>
        )}
      </AnimatePresence>
    </>
  )
}

export function ToggleRow({ label, hint, cfg, field, patch, danger }: {
  label: string
  hint?: string
  cfg: Record<string, unknown>
  field: string
  patch: (k: string, v: never, cb: () => void, label?: string) => void
  danger?: boolean
}) {
  const [saved, setSaved] = useState(false)
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
  const on = Boolean(cfg[field])
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        {
}
        {danger && on && <AlertTriangle size={14} className="text-warn" role="img" aria-label="Relaxes a safety default" />}
        <Toggle on={on} onChange={(v) => patch(field, v as never, flash, label)} label={label} />
      </div>
    </Row>
  )
}

export function NumberRow({ label, hint, cfg, field, min, max, step = 1, patch }: {
  label: string
  hint?: string
  cfg: Record<string, unknown>
  field: string
  min: number
  max: number
  step?: number
  patch: (k: string, v: never, cb: () => void, label?: string) => void
}) {
  const [saved, setSaved] = useState(false)
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
  const raw = Number(cfg[field])
  const value = Number.isFinite(raw) ? raw : min
  return (
    <Field label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <NumberField value={value} min={min} max={max} step={step} onChange={(n) => patch(field, n as never, flash, label)} ariaLabel={label} />
        <SavedToast show={saved} />
      </div>
    </Field>
  )
}

export function StrListField({ label, hint, cfg, field, patch, placeholder = 'Add…' }: {
  label: string
  hint?: string
  cfg: Record<string, unknown>
  field: string
  patch: (k: string, v: never, cb: () => void, label?: string) => void
  placeholder?: string
}) {
  const [saved, setSaved] = useState(false)
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
  const list = Array.isArray(cfg[field]) ? (cfg[field] as string[]) : []
  const [adding, setAdding] = useState('')
  const commit = (next: string[]) => patch(field, next as never, flash, label)
  const add = () => { commit([...list, adding.trim()]); setAdding('') }
  return (
    <Field label={label} hint={hint}>
      <div className="flex flex-wrap items-center gap-1.5">
        {list.map((v) => (
          <span key={v} data-type="caption" className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2.5 py-1 text-on-surface font-mono">
            {v}
            <button type="button" onClick={() => commit(list.filter((x) => x !== v))} aria-label={`Remove ${v}`} className="text-on-surface-low hover:text-on-surface"><X size={12} /></button>
          </span>
        ))}
        {
}
        <input value={adding} onChange={(e) => setAdding(e.target.value)} placeholder={placeholder}
          aria-label={`Add to ${label.toLowerCase()}`}
          onKeyDown={(e) => { if (e.key === 'Enter' && adding.trim()) add() }}
          data-type="caption" className="h-8 w-40 rounded-md bg-surface-high px-2 text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
        {adding.trim() && (
          <SquareIconButton icon={Plus} iconSize={15} label={`Add ${label.toLowerCase()}`} onClick={add} />
        )}
        <SavedToast show={saved} />
      </div>
    </Field>
  )
}
