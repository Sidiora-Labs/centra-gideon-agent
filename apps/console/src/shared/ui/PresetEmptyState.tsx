import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { TileButton } from './TileButton'
import { fvs } from '../theme/fontWeight'

export interface PresetDef<P> {
  id: string
  icon: LucideIcon
  title: string
  summary: string
  description: string
  prefill: P
}

export function PresetCard<P>({ icon: Icon, title, summary, description, prefill, onPick }: {
  icon: LucideIcon
  title: string
  summary: string
  description: string
  prefill: P
  onPick: (prefill: P) => void
}) {
  return (
    <TileButton
      ariaLabel={`${title} — ${summary}`}
      onClick={() => onPick(prefill)}
      className="h-full gap-s p-l"
    >
      <span
        className="mb-1 inline-flex size-9 items-center justify-center rounded-lg"
        style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}
      >
        <Icon size={18} className="text-primary" aria-hidden />
      </span>
      <span data-type="title-m" className="text-on-surface" style={fvs(550)}>{title}</span>
      <span data-type="body-s" className="text-primary">{summary}</span>
      <span data-type="body-s" className="text-on-surface-low">{description}</span>
    </TileButton>
  )
}

export function PresetEmptyState<P>({ title, hint, presets, onPick, footer }: {
  title: string
  hint?: string
  presets: PresetDef<P>[]
  onPick: (prefill: P) => void
  footer?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-l py-2xl">
      <div className="text-center">
        <h2 data-type="headline-s" className="text-on-surface">{title}</h2>
        {hint && <p data-type="body-m" className="mt-1 mx-auto max-w-[520px] text-on-surface-low">{hint}</p>}
      </div>
      {
}
      <div className="grid w-full max-w-[640px] gap-s sm:grid-cols-2">
        {presets.map((p) => (
          <PresetCard
            key={p.id}
            icon={p.icon}
            title={p.title}
            summary={p.summary}
            description={p.description}
            prefill={p.prefill}
            onPick={onPick}
          />
        ))}
      </div>
      {footer}
    </div>
  )
}
