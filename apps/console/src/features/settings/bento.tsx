import { type ReactNode, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowRight, Loader2, type LucideIcon } from 'lucide-react'
import { Toggle } from '../../shared/ui/Toggle'
import { StatusPill as UiStatusPill } from '../../shared/ui/StatusPill'
import { StaleNotice } from '../../shared/ui/StaleNotice'
import { InlineError } from '../../shared/ui/InlineError'
import { readableErrText } from '../../shared/data/errText'
import type { QueryStatus } from '../../shared/data/data'
import { spring, expr } from '../../shared/theme/motion'
import { fvs, withWeight } from '../../shared/theme/fontWeight'

export type BentoSize = 'sm' | 'md' | 'lg' | 'wide' | 'tall'

export function Highlight({ text, query }: { text: string; query: string }) {
  const q = query.trim()
  if (!q) return <>{text}</>
  const lower = text.toLowerCase()
  const ql = q.toLowerCase()
  const out: ReactNode[] = []
  let i = 0
  let n = 0
  while (i < text.length) {
    const hit = lower.indexOf(ql, i)
    if (hit < 0) { out.push(text.slice(i)); break }
    if (hit > i) out.push(text.slice(i, hit))
    out.push(
      <mark key={n++} className="rounded-[3px] bg-primary/30 px-0.5 text-on-surface" style={{ color: 'inherit' }}>
        {text.slice(hit, hit + q.length)}
      </mark>,
    )
    i = hit + q.length
  }
  return <>{out}</>
}

export function BentoCard({ icon: Icon, title, query, onClick, loading, status, error, refresh, operation, stale, accent, footer, rows, children }: {
  icon: LucideIcon
  title: string
  query?: string
  onClick: () => void
  loading?: boolean
  status?: QueryStatus
  error?: unknown
  refresh?: () => void
  operation?: string
  stale?: boolean
  accent?: string
  footer?: ReactNode
  rows?: number
  children?: ReactNode
}) {
  const tint = accent || 'var(--color-primary)'
  const failed = status === 'error'
  const waiting = status ? status === 'loading' : !!loading
  const serverWords = readableErrText(error) || "The server didn't respond."
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={spring.spatialDefault}
      whileHover={{ y: -expr(4, 0.3), boxShadow: 'var(--shadow-lift)' }}
      className="group relative flex w-full flex-col rounded-xl bg-surface-container p-4 transition-colors focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary hover:bg-surface-high"
    >
      {
}
      <button type="button" onClick={onClick} aria-label={`Open ${title} settings`}
        aria-busy={waiting || undefined}
        className="absolute inset-0 z-0 rounded-xl outline-none" />
      { }
      <div className="pointer-events-none relative z-10 flex min-h-0 flex-col">
        <div className="mb-3 flex items-center gap-2">
          <span className="grid size-7 shrink-0 place-items-center rounded-md" style={{ background: `color-mix(in srgb, ${tint} 16%, transparent)`, color: tint }}>
            <Icon size={15} />
          </span>
          <span data-type="title-m" className="flex-1 truncate text-on-surface" style={fvs(600)}>
            {query ? <Highlight text={title} query={query} /> : title}
          </span>
          <StaleNotice stale={!waiting && !!stale} what={title.toLowerCase()} announce={false} className="shrink-0" />
          <ArrowRight size={14} className="shrink-0 text-on-surface-low transition-transform group-hover:translate-x-0.5" />
        </div>
        {failed
          ? <div className="pointer-events-auto" onClick={(e) => e.stopPropagation()}>
              <InlineError icon multiline onRetry={refresh}>Couldn&rsquo;t load {operation ?? title.toLowerCase()}: {serverWords}</InlineError>
            </div>
          : waiting
          ? <CardSkeleton rows={rows ?? 2} />
          : <div className="flex min-h-0 flex-1 flex-col">{children}</div>}
        {footer && <div data-type="caption" className="mt-2 text-on-surface-low">{footer}</div>}
      </div>
    </motion.div>
  )
}

export function CardSkeleton({ rows = 2 }: { rows?: number }) {
  const widths = ['w-2/3', 'w-1/2', 'w-3/5', 'w-2/5', 'w-3/4']
  return (
    <div className="flex-1 space-y-2" aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className={`h-3 ${widths[i % widths.length]} animate-pulse rounded bg-surface-high/70`} />
      ))}
    </div>
  )
}


export function Switch({ on, onToggle, label, disabled }: {
  on: boolean; onToggle: (next: boolean) => void | Promise<void>; label?: string; disabled?: boolean
}) {
  const [busy, setBusy] = useState(false)
  const run = async () => {
    if (busy || disabled) return
    setBusy(true)
    try { await onToggle(!on) } finally { setBusy(false) }
  }
  return (
    <span className="pointer-events-auto relative inline-flex" onClick={(e) => e.stopPropagation()}>
      <Toggle on={on} onChange={run} label={label} disabled={disabled || busy} size="sm" />
      {busy && (
        <span className="pointer-events-none absolute inset-0 grid place-items-center">
          <Loader2 size={9} className="animate-spin text-on-surface-low" />
        </span>
      )}
    </span>
  )
}

export function SegToggle<T extends string>({ value, options, onPick, ariaLabel }: {
  value: T; options: { key: T; label: string }[]; onPick: (v: T) => void | Promise<void>; ariaLabel: string
}) {
  const [busy, setBusy] = useState(false)
  const pick = async (e: React.MouseEvent, k: T) => {
    e.stopPropagation()
    if (busy || k === value) return
    setBusy(true)
    try { await onPick(k) } finally { setBusy(false) }
  }
  return (
    <div className="pointer-events-auto inline-flex rounded-pill bg-surface-high p-0.5" style={{ opacity: busy ? 0.7 : 1 }}>
      {options.map((o) => (
        <button key={o.key} type="button" onClick={(e) => pick(e, o.key)}
          aria-label={`${ariaLabel}: ${o.label}`} aria-pressed={o.key === value}
          data-type="caption" className="rounded-pill px-2 h-6 -my-px transition-colors"
          style={o.key === value ? { background: 'var(--color-surface-highest)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function InlineSelect({ value, options, onPick, ariaLabel }: {
  value: string; options: { value: string; label: string }[]; onPick: (v: string) => void | Promise<void>; ariaLabel?: string
}) {
  const [busy, setBusy] = useState(false)
  return (
    <select value={value} aria-label={ariaLabel} disabled={busy}
      onClick={(e) => e.stopPropagation()}
      onChange={async (e) => { e.stopPropagation(); setBusy(true); try { await onPick(e.target.value) } finally { setBusy(false) } }}
      data-type="caption" className="pointer-events-auto max-w-[10rem] truncate rounded-md bg-surface-high px-2 h-7 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary disabled:opacity-60">
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  )
}

export function BigStat({ value, caption, tone }: { value: ReactNode; caption: ReactNode; tone?: string }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="tabular-nums text-on-surface text-[1.75rem] leading-none" style={withWeight({ color: tone }, 600)}>{value}</span>
      <span data-type="body-s" className="text-on-surface-low">{caption}</span>
    </div>
  )
}

export function KVList({ rows, query }: { rows: { k: string; v: ReactNode; vText?: string; mono?: boolean; control?: boolean }[]; query?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      {rows.map((r, i) => (
        <div key={i} data-type="body-s" className="flex min-h-[1.75rem] items-center justify-between gap-2">
          <span className="shrink-0 text-on-surface-low">{query ? <Highlight text={r.k} query={query} /> : r.k}</span>
          <span data-type={r.mono ? 'caption' : undefined} className={`min-w-0 text-right text-on-surface ${r.control ? 'shrink-0' : 'truncate'} ${r.mono ? 'font-mono' : 'tabular-nums'}`}>
            {query && r.vText !== undefined ? <Highlight text={r.vText} query={query} /> : r.v}
          </span>
        </div>
      ))}
    </div>
  )
}

export function StatusPill({ label, tone, query }: { label: string; tone?: 'ok' | 'warn' | 'muted' | 'primary'; query?: string }) {
  const content = query ? <Highlight text={label} query={query} /> : label
  if (!tone || tone === 'muted') {
    return (
      <span data-type="caption" className="inline-flex items-center gap-1 rounded-pill px-2 h-[22px]"
        style={{ background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
        {content}
      </span>
    )
  }
  return (
    <UiStatusPill tone={tone} pad={false} className="gap-1 px-2 h-[22px]">
      {content}
    </UiStatusPill>
  )
}

export function ChipRow({ chips, query }: { chips: { label: string; tone?: 'ok' | 'warn' | 'muted' | 'primary' }[]; query?: string }) {
  return (
    <div className="flex flex-wrap gap-1">
      {chips.map((c, i) => <StatusPill key={i} label={c.label} tone={c.tone} query={query} />)}
    </div>
  )
}
