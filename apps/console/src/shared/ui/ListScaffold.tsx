import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { AlertTriangle, RotateCcw, type LucideIcon } from 'lucide-react'
import { TopBar } from './TopBar'
import { Spark } from './Spark'
import { Button } from './Button'
import { spring, expr, useReducedMotion } from '../theme/motion'
import { readableErrText } from '../data/errText'
import { PageTitle } from './PageTitle'
import { Surface } from './Surface'
import { RowHitTarget } from './RowHitTarget'
import { cx } from './cx'

export function ListScaffold({ title, right, children, bodyClassName }: {
  title: ReactNode; right?: ReactNode; children: ReactNode; bodyClassName?: string
}) {
  const content = <div className={bodyClassName ?? 'mx-auto px-l py-2xl'} style={{ maxWidth: 'var(--content-width)' }}>{children}</div>
  return <div className="flex h-full min-h-0 flex-col" data-list-page>
    <TopBar left={<PageTitle>{title}</PageTitle>} right={right} />
    <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">{content}</div>
  </div>
}

function CollectionMessage({ title, detail, emblem, action, alert = false }: {
  title: ReactNode; detail?: ReactNode; emblem: ReactNode; action?: ReactNode; alert?: boolean
}) {
  return <div role={alert ? 'alert' : undefined} data-visual-state={alert ? 'failed' : 'empty'} className="flex flex-col items-center gap-l py-2xl text-center">
    {emblem}
    <div className="space-y-1">
      <h2 data-type="headline-s" className="text-on-surface">{title}</h2>
      {detail && <p data-type="body-m" className="mt-1 max-w-[420px] text-on-surface-low">{detail}</p>}
    </div>
    {action}
  </div>
}

export function LoadError({ what, error, onRetry }: { what: string; error?: unknown; onRetry?: () => void }) {
  const message = readableErrText(error) || "The server didn't respond — this is just a load error, and nothing was lost."
  return <CollectionMessage alert title={<>Couldn't load your {what}</>} detail={message}
    emblem={<span className="grid size-12 place-items-center rounded-xl bg-danger/10"><AlertTriangle size={32} className="text-danger" aria-hidden /></span>}
    action={onRetry && <Button size="sm" onClick={onRetry}><RotateCcw size={15} aria-hidden /> Retry</Button>} />
}

export function EmptyState({ icon: Icon, title, hint, action }: {
  icon?: LucideIcon; title: string; hint?: string
  action?: { label: string; onClick: () => void; icon?: LucideIcon }
}) {
  const ActionIcon = action?.icon
  const emblem = Icon
    ? <span className="inline-flex size-12 items-center justify-center rounded-xl ring-1 ring-primary/10"
      style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}><Icon size={26} className="text-primary" aria-hidden /></span>
    : <Spark size={36} />
  return <CollectionMessage title={title} detail={hint} emblem={emblem}
    action={action && <Button onClick={action.onClick}>{ActionIcon && <ActionIcon size={16} aria-hidden />} {action.label}</Button>} />
}

export function ListRow({ index = 0, onClick, children, accent, label }: {
  index?: number; onClick?: () => void; children: ReactNode; accent?: string; label?: string
}) {
  const reduced = useReducedMotion()
  const interactive = !!onClick
  const movement = interactive && !reduced
  return <motion.div
    initial={reduced ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
    exit={reduced ? { opacity: 0, transition: { duration: 0 } } : { opacity: 0, height: 0, marginTop: 0, transition: spring.spatialFast }}
    transition={reduced ? { duration: 0 } : { ...spring.spatialDefault, delay: Math.min(Math.max(0, index) * 0.03, 0.3) }}
    whileHover={movement ? { y: -expr(3, 0.3), boxShadow: 'var(--shadow-lift)' } : undefined}
    whileTap={movement ? { scale: 1 - expr(0.01, 0.3) } : undefined}
    onClick={onClick} tabIndex={interactive ? -1 : undefined}
    className={cx('group relative isolate flex items-center gap-l overflow-hidden rounded-lg border border-outline-variant/20 bg-surface-container px-l py-l text-left transition-colors hover:bg-surface-high',
      interactive && 'cursor-pointer has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary')}>
    {interactive && <RowHitTarget label={label ?? ''} />}
    {accent && <span aria-hidden className="absolute bottom-s left-0 top-s w-[3px] rounded-r-full" style={{ background: accent }} />}
    {children}
  </motion.div>
}

function loadingText(what?: string) { return what ? `Loading ${what}…` : 'Loading…' }

export function Loading({ what }: { what?: string }) {
  return <div role="status" aria-busy="true" data-visual-state="waiting" data-type="body-s" className="text-on-surface-low">{loadingText(what)}</div>
}

export function Skeleton({ className }: { className: string }) {
  return <div aria-hidden="true" data-visual-state="waiting" className={cx('skeleton rounded-md', className)} />
}

export function LoadingStatus({ what }: { what?: string }) {
  return <span className="sr-only">{loadingText(what)}</span>
}

function PlaceholderRegion({ what, className, children }: { what?: string; className?: string; children: ReactNode }) {
  return <div role="status" aria-busy="true" data-visual-state="waiting" className={className}><LoadingStatus what={what} />{children}</div>
}

function repeat(count: number, render: (index: number) => ReactNode) {
  return Array.from({ length: Math.max(0, Math.floor(count)) }, (_, index) => render(index))
}

function PlaceholderTitle() {
  return <div className="mb-l space-y-s"><Skeleton className="h-5 w-40" /><Skeleton className="h-3 w-2/3" /></div>
}

export function ListSkeleton({ rows = 6, what }: { rows?: number; what?: string }) {
  return <PlaceholderRegion what={what} className="flex flex-col gap-s">
    {repeat(rows, index => <div key={index} className="flex items-center gap-l rounded-lg border border-outline-variant/20 bg-surface-container px-l py-l">
      <Skeleton className="size-10 shrink-0 rounded-lg" />
      <div className="min-w-0 flex-1 space-y-s"><Skeleton className="h-3.5 w-1/3" /><Skeleton className="h-3 w-2/3" /></div>
    </div>)}
  </PlaceholderRegion>
}

export function FormSkeleton({ sections = 2, rows = 3, title = true, what }: { sections?: number; rows?: number; title?: boolean; what?: string }) {
  return <PlaceholderRegion what={what}>
    {title && <PlaceholderTitle />}
    {repeat(sections, section => <section key={section} className="mb-2xl">
      <Skeleton className="mb-m h-4 w-32" />
      <Surface tone="container" radius="lg" className="px-l py-xs">
        {repeat(rows, row => <div key={row} className="flex items-center justify-between gap-l border-b border-outline-variant/20 py-3 last:border-0">
          <div className="min-w-0 flex-1 space-y-xs"><Skeleton className="h-3.5 w-1/3" /><Skeleton className="h-3 w-1/2" /></div>
          <Skeleton className="h-6 w-16 shrink-0 rounded-pill" />
        </div>)}
      </Surface>
    </section>)}
  </PlaceholderRegion>
}

export function CardGridSkeleton({ cards = 4, cols = 2, title = true, what }: { cards?: number; cols?: number; title?: boolean; what?: string }) {
  return <PlaceholderRegion what={what}>
    {title && <PlaceholderTitle />}
    <div className="grid gap-m" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
      {repeat(cards, index => <div key={index} className="space-y-m rounded-lg border border-outline-variant/20 bg-surface-container px-l py-l">
        <div className="flex items-center gap-s"><Skeleton className="size-5 rounded" /><Skeleton className="h-3.5 w-24" /></div>
        <Skeleton className="h-7 w-20" /><Skeleton className="h-3 w-2/3" />
      </div>)}
    </div>
  </PlaceholderRegion>
}
