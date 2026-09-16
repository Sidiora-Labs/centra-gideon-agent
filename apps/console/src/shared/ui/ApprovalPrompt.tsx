import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { ShieldQuestion, type LucideIcon } from 'lucide-react'
import { messageEnter } from '../theme/motion'
import { fvs } from '../theme/fontWeight'

export type ApprovalDensity = 'compact' | 'roomy'

export interface ApprovalChoice {
  key: string
  label: string
  icon: LucideIcon
  tone?: 'primary' | 'neutral' | 'danger'
  name?: string
  onClick: () => void
  busy?: boolean
}

export function ApprovalPrompt({
  tool, args, purpose, badge, meta, scope, choices, density = 'compact', className,
}: {
  tool: string
  args?: string
  purpose?: string
  badge?: ReactNode
  meta?: ReactNode
  scope?: ReactNode
  choices: ApprovalChoice[]
  density?: ApprovalDensity
  className?: string
}) {
  const roomy = density === 'roomy'
  return (
    <motion.div variants={messageEnter} initial="initial" animate="animate"
      role="group" aria-label={`Permission needed to run ${tool}`}
      className={`${roomy ? '' : 'my-1.5 '}overflow-hidden rounded-xl border${className ? ` ${className}` : ''}`}
      style={{ borderRadius: 'var(--radius-md)', borderColor: 'color-mix(in srgb, var(--color-warn) 40%, transparent)', background: 'color-mix(in srgb, var(--color-warn) 8%, transparent)' }}>
      <div className={roomy ? 'flex items-start gap-3 px-l pt-l' : 'flex items-start gap-2 px-3 pt-2.5'}>
        <ShieldQuestion size={roomy ? 18 : 15} className="mt-0.5 shrink-0" aria-hidden style={{ color: 'var(--color-warn)' }} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <div role="alert" data-type={roomy ? 'title-m' : 'label-s'} className="text-on-surface" style={fvs(500)}>Permission needed</div>
            {badge}
          </div>
          {roomy ? (
            <>
              <div data-type="body-m" className="mt-1 break-all font-mono text-on-surface">{tool}</div>
              {args && (
                <pre tabIndex={0} role="group" aria-label="Tool arguments" data-type="caption"
                  className="mt-s max-h-[14rem] overflow-auto whitespace-pre-wrap break-all rounded-md bg-surface-high p-s font-mono text-on-surface-var">{args}</pre>
              )}
            </>
          ) : (
            <div data-type="caption" className="mt-0.5 truncate font-mono text-on-surface-var">{tool}{args ? `(${args.replace(/\s+/g, ' ').slice(0, 60)})` : ''}</div>
          )}
          {purpose && <p data-type={roomy ? 'body-s' : 'caption'} className="mt-1 text-on-surface-low">{purpose}</p>}
          {meta}
          {
}
          {scope}
        </div>
      </div>
      <div className={roomy ? 'flex flex-wrap items-center gap-s px-l py-l' : 'flex flex-wrap items-center gap-1.5 px-3 py-2.5'}>
        {choices.map((c) => <ApprovalChoiceButton key={c.key} choice={c} roomy={roomy} />)}
      </div>
    </motion.div>
  )
}

function ApprovalChoiceButton({ choice, roomy }: { choice: ApprovalChoice; roomy: boolean }) {
  const { icon: Icon, label, tone = 'neutral', name, onClick, busy } = choice
  const palette = tone === 'primary'
    ? { background: 'var(--color-primary)', color: 'var(--color-on-primary)' }
    : tone === 'danger'
      ? { background: 'color-mix(in srgb, var(--color-danger) 14%, transparent)', color: 'var(--color-danger)' }
      : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }
  return (
    <button type="button" onClick={onClick} disabled={busy} aria-busy={busy || undefined}
      title={name || label} aria-label={name || undefined}
      data-type={roomy ? 'body-m' : 'caption'}
      className={`inline-flex items-center justify-center gap-1 rounded-pill transition-colors disabled:opacity-50 ${roomy ? 'h-11 min-w-[6.5rem] gap-2 px-l' : 'h-7 px-2.5'}`}
      style={palette}>
      <Icon size={roomy ? 16 : 12} aria-hidden /> {label}
    </button>
  )
}
