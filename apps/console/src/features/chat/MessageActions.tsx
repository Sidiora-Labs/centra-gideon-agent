import { useState } from 'react'
import { Copy, Check, RotateCcw, GitBranch, Volume2, Square, Pencil, ChevronLeft, ChevronRight, Rewind } from 'lucide-react'
import { unavailableWhen } from '../../shared/ui/unavailable'
import { clockTime, fullStamp, isoStamp } from '../../shared/data/epoch'
import { copyText } from '../../app/shell/clipboard'

const REVEAL = 'flex items-center gap-0.5 translate-y-0.5 opacity-0 transition duration-150'
  + ' group-hover/msg:translate-y-0 group-hover/msg:opacity-100 focus-within:translate-y-0 focus-within:opacity-100'

function Stamp({ ts }: { ts?: string }) {
  const shown = clockTime(ts)
  if (!shown) return null
  const iso = isoStamp(ts)
  return (
    <time data-type="caption" className="select-none tabular-nums text-on-surface-low"
      {...(iso ? { dateTime: iso } : {})} title={fullStamp(ts)}>{shown}</time>
  )
}

export function AssistantActions({ text, isLast, speaking, canFork = true, variantCount = 0, variantIdx = 0, ts, onCopy, onRegenerate, onFork, onSpeak, onSwitchVariant }: {
  text: string
  isLast: boolean
  ts?: string
  speaking?: boolean
  canFork?: boolean
  variantCount?: number
  variantIdx?: number
  onCopy: () => void
  onRegenerate: () => void
  onFork: () => void
  onSpeak: () => void
  onSwitchVariant?: (index: number) => void
}) {
  const [copied, setCopied] = useState(false)
  const copy = async () => { if (await copyText(text, 'this message')) { setCopied(true); setTimeout(() => setCopied(false), 1500) }; onCopy() }
  const hasVariants = variantCount > 1 && !!onSwitchVariant
  return (
    <div className="mt-m flex items-center gap-1.5">
      <Stamp ts={ts} />
      <div className={REVEAL}>
        {hasVariants && <VariantSwitcher count={variantCount} idx={variantIdx} onSwitch={onSwitchVariant!} />}
        <ActBtn icon={copied ? Check : Copy} label={copied ? 'Copied' : 'Copy'} onClick={copy} done={copied} />
        {isLast && <ActBtn icon={RotateCcw} label="Regenerate" onClick={onRegenerate} />}
        {canFork && <ActBtn icon={GitBranch} label="Branch from here" onClick={onFork} />}
        <ActBtn icon={speaking ? Square : Volume2} label={speaking ? 'Stop' : 'Speak'} onClick={onSpeak} active={speaking} />
      </div>
    </div>
  )
}

function VariantSwitcher({ count, idx, onSwitch }: { count: number; idx: number; onSwitch: (index: number) => void }) {
  const atStart = idx <= 0
  const atEnd = idx >= count - 1
  const arrow = 'inline-flex h-8 w-6 items-center justify-center rounded-md transition-colors hover:bg-surface-high hover:text-on-surface aria-disabled:opacity-40 aria-disabled:hover:bg-transparent aria-disabled:hover:text-on-surface-low aria-disabled:cursor-default'
  return (
    <div className="inline-flex items-center gap-0.5 rounded-md pr-1 text-on-surface-low" title={`Answer ${idx + 1} of ${count}`}>
      <button type="button" onClick={() => !atStart && onSwitch(idx - 1)}
        aria-label="Previous answer" className={arrow}
        {...unavailableWhen(atStart, 'Already at the first answer', { title: 'Previous answer' })}>
        <ChevronLeft size={14} />
      </button>
      <span data-type="caption" className="min-w-[2.1rem] select-none text-center tabular-nums" aria-live="polite">{idx + 1}/{count}</span>
      <button type="button" onClick={() => !atEnd && onSwitch(idx + 1)}
        aria-label="Next answer" className={arrow}
        {...unavailableWhen(atEnd, 'Already at the last answer', { title: 'Next answer' })}>
        <ChevronRight size={14} />
      </button>
    </div>
  )
}

export function UserActions({ text, canFork = true, canRewind = false, ts, onEdit, onRewind, onFork }: { text: string; canFork?: boolean; canRewind?: boolean; ts?: string; onEdit: () => void; onRewind?: () => void; onFork: () => void }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => { if (await copyText(text, 'this message')) { setCopied(true); setTimeout(() => setCopied(false), 1500) } }
  return (
    <div className="mt-1.5 flex items-center justify-end gap-1.5">
      <div className={REVEAL}>
        <ActBtn icon={copied ? Check : Copy} label={copied ? 'Copied' : 'Copy'} onClick={copy} done={copied} />
        <ActBtn icon={Pencil} label="Edit & resend" onClick={onEdit} />
        {canRewind && onRewind && <ActBtn icon={Rewind} label="Rewind to here" onClick={onRewind} />}
        {canFork && <ActBtn icon={GitBranch} label="Branch from here" onClick={onFork} />}
      </div>
      <Stamp ts={ts} />
    </div>
  )
}

function ActBtn({ icon: Icon, label, onClick, done, active }: { icon: typeof Copy; label: string; onClick: () => void; done?: boolean; active?: boolean }) {
  return (
    <button type="button" onClick={onClick} title={label} aria-label={label}
      data-type="caption"
      className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface"
      style={done ? { color: 'var(--color-ok)' } : active ? { color: 'var(--color-primary)' } : undefined}>
      <Icon size={14} className={active ? 'fill-current' : ''} />
    </button>
  )
}
