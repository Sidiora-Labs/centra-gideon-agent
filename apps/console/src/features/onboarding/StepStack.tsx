import { forwardRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Check, type LucideIcon } from 'lucide-react'
import { withWeight } from '../../shared/theme/fontWeight'
import { spring } from '../../shared/theme/motion'

export type StepState = 'upcoming' | 'active' | 'done'
type StepRowProps = {
  index: number; icon: LucideIcon; title: string; subtitle?: string; state: StepState
  doneSummary?: string; onActivate?: () => void; children?: React.ReactNode
}
const SURFACES = {
  active: { background: 'var(--color-surface-container)', border: '1px solid var(--color-outline)', boxShadow: 'var(--shadow-rest)' },
  done: { background: 'transparent', border: '1px solid transparent', boxShadow: 'none' },
  upcoming: { background: 'transparent', border: '1px solid transparent', boxShadow: 'none' },
}
export const StepRow = forwardRef<HTMLLIElement, StepRowProps>(function StepRow(props, ref) {
  const { index, icon: Icon, title, subtitle, state, doneSummary, onActivate, children } = props
  const active = state === 'active'
  const done = state === 'done'
  const revisitable = !active && done && !!onActivate
  const Header = revisitable ? motion.button : motion.div
  const caption = active ? subtitle : done ? doneSummary : undefined
  const badge = done ? 'var(--color-success)' : active ? 'var(--color-primary)' : 'var(--color-surface-high)'
  const action = revisitable ? { type: 'button' as const, onClick: onActivate, 'aria-label': `Go back to step ${index + 1}: ${title}` } : {}
  return <motion.li ref={ref} layout aria-current={active ? 'step' : undefined}
    transition={spring.spatialDefault} className="list-none overflow-hidden"
    style={{ ...SURFACES[state], borderRadius: 'var(--radius-xli)' }}>
    <Header {...action} layout="position" style={{ borderRadius: 'var(--radius-xli)' }}
      className={`flex w-full items-center gap-m px-l py-m text-left focus-visible:-outline-offset-2 ${revisitable ? 'cursor-pointer' : 'cursor-default'}`}>
      <span className="grid size-9 shrink-0 place-items-center rounded-xl transition-colors"
        style={{ background: badge, color: active || done ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)' }}>
        <AnimatePresence initial={false} mode="wait">
          <motion.span key={done ? 'complete' : 'step'}
            initial={done ? { scale: 0, rotate: -30 } : { scale: 0.6, opacity: 0 }}
            animate={{ scale: 1, rotate: 0, opacity: 1 }} transition={spring.spatialFast}>
            {done ? <Check size={18} /> : <Icon size={17} />}
          </motion.span>
        </AnimatePresence>
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-s">
          <span className="text-on-surface" style={withWeight({ fontSize: active ? '1.0625rem' : '0.9375rem' }, 600)}>{title}</span>
          <span className="text-on-surface-low text-[0.75rem]">Step {index + 1}</span>
        </div>
        <AnimatePresence initial={false} mode="wait">
          {caption && <motion.p key={active ? 'instruction' : 'summary'} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="mt-1 text-[0.8125rem]" style={{ color: done ? 'var(--color-success)' : 'var(--color-on-surface-low)' }}>{caption}</motion.p>}
        </AnimatePresence>
      </div>
    </Header>
    <AnimatePresence initial={false}>
      {active && children && <motion.div key="body" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
        transition={{ height: spring.spatialDefault, opacity: { duration: 0.18 } }}>
        <div className="mx-l mb-l border-t border-outline/20 pt-m sm:ml-[4.75rem]">{children}</div>
      </motion.div>}
    </AnimatePresence>
  </motion.li>
})
