import { useEffect, useRef, useState, type ReactNode, type Ref } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Brain, ChevronRight, Gauge, Sparkles, type LucideIcon } from 'lucide-react'
import { spring } from '../../shared/theme/motion'
import { TextLink } from '../../shared/ui/TextLink'
import { learnedSurface } from './chatTypes'

export function ContextLedger({ fed, learned, learnedOrigin, stats }: { fed?: string; learned?: string; learnedOrigin?: string; stats?: string }) {
  const [open, setOpen] = useState(false)
  const learnedRowRef = useRef<HTMLDivElement>(null)
  const fedChars = fed?.match(/([\d,]+)\s*chars/)?.[1] ?? ''
  const learnedText = learned?.replace(/^Learned:\s*/i, '').trim() ?? ''
  const surface = learnedSurface(learnedOrigin)
  const learnedHref = surface?.href ?? null

  useEffect(() => {
    if (!open || !learnedHref) return
    const link = learnedRowRef.current?.querySelector('a')
    if (!link) return
    link.scrollIntoView({ block: 'nearest' })
    link.focus({ preventScroll: true })
  }, [open, learnedHref])

  const summary = open
    ? 'Context & learning'
    : [fed && 'recalled context', learned && 'learned 1', stats && 'telemetry'].filter(Boolean).join(' · ') || 'Turn details'
  const collapsedTitle = surface
    ? 'What fed this turn · what was learned — opens and jumps to where you can review it'
    : 'What fed this turn · what was learned'
  return (
    <div className="mt-2 mb-1">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        data-type="caption"
        className="flex items-center gap-1.5 rounded-pill text-on-surface-low/80 transition-colors hover:text-on-surface-low"
        title={open ? 'Hide what fed this turn and what was learned' : collapsedTitle}>
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={spring.spatialFast} className="shrink-0 opacity-60">
          <ChevronRight size={11} />
        </motion.span>
        <Brain size={11} className="shrink-0 opacity-70" />
        <span>{summary}</span>
        {!open && learned && <Sparkles size={11} className="shrink-0 text-primary/80" />}
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
            transition={spring.spatialFast} className="overflow-hidden">
            <div data-type="caption" className="mt-1.5 ml-1.5 flex flex-col gap-1.5 border-l border-outline-variant/40 pl-3 text-on-surface-low">
              {fed && (
                <LedgerRow icon={Brain} label="Fed this turn">
                  Recalled relevant context{fedChars ? ` · ${fedChars} chars` : ''} — saved memories, learned lessons, earlier conversation, and episodic history, assembled and prepended to the prompt.
                </LedgerRow>
              )}
              {learned && (
                <LedgerRow icon={Sparkles} label="Learned & saved" contentRef={learnedRowRef}>
                  <span className="text-on-surface-var">{learnedText || 'A preference was captured.'}</span>
                  {surface && <>{' '}<TextLink href={surface.href}>{surface.label}</TextLink></>}
                </LedgerRow>
              )}
              {stats && (
                <LedgerRow icon={Gauge} label="Telemetry">
                  <span className="whitespace-pre-wrap break-words">{stats}</span>
                </LedgerRow>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function LedgerRow({ icon: Icon, label, children, contentRef }: { icon: LucideIcon; label: string; children: ReactNode; contentRef?: Ref<HTMLDivElement> }) {
  return (
    <div className="flex items-start gap-1.5">
      <Icon size={11} className="mt-[0.15rem] shrink-0 opacity-70" />
      <div ref={contentRef} className="min-w-0">
        {
}
        <span className="fw-500 text-on-surface-low/90">{label}:</span>{' '}
        {children}
      </div>
    </div>
  )
}
