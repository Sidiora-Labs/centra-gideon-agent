import { useSetupTrials, type TryCardState as CardState } from './trySetupState'
import { motion } from 'framer-motion'
import { BookOpen, BellRing, Repeat, Check, ArrowRight, ExternalLink } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { InlineError } from '../../shared/ui/InlineError'
import { TextLink } from '../../shared/ui/TextLink'
import { listItemEnter, stagger, spring } from '../../shared/theme/motion'
import { fvs } from '../../shared/theme/fontWeight'
import type { OnboardingStatePatch } from '../../shared/data/api'
import {
  type SettingsTarget, type TryOneId, type TryOneOutcome,
} from './tryOneFlows'

interface CardDef {
  id: TryOneId
  icon: LucideIcon
  title: string

  blurb: string

  action: string
}

const CARDS: CardDef[] = [
  {
    id: 'knowledge', icon: BookOpen, title: 'Teach it something',
    blurb: 'Saves a real note to Knowledge, then asks your library a question and shows the passage that answered it.',
    action: 'Save and ask',
  },
  {
    id: 'trigger', icon: BellRing, title: 'Set a reminder',
    blurb: 'Creates a real 9:00 AM reminder and fires it once now, so you see exactly what it will say.',
    action: 'Create and fire once',
  },
  {
    id: 'loop', icon: Repeat, title: 'Start a loop',
    blurb: 'Creates a one-cycle goal loop and starts it for real. It stops on its own.',
    action: 'Start it',
  },
]

export function TryOneStep({ onProgress, onDone, onSkip, onExitTo }: {

  onProgress: (patch: OnboardingStatePatch) => void

  onDone: (summary: string) => void

  onSkip: () => void

  onExitTo: (path: string) => void
}) {
  const { stateOf, run, doneCount } = useSetupTrials(onProgress)

  return (
    <div className="grid gap-l">
      <p className="text-on-surface-var text-[0.8125rem]">
        Each of these runs for real on this machine — nothing is a preview. Try one, try all
        three, or skip straight to the dashboard.
      </p>

      <motion.div className="flex flex-col gap-s" initial="initial" animate="animate"
        variants={{ animate: { transition: stagger(0.05) } }}>
        {CARDS.map((c) => (
          <TryOneCard key={c.id} def={c} state={stateOf(c.id)}
            onRun={() => run(c.id)} onExitTo={onExitTo} />
        ))}
      </motion.div>

      <div className="flex items-center justify-end gap-m border-t border-outline/25 pt-m">
        <Button variant="primary" size="md"
          onClick={() => onDone(doneCount ? `${doneCount} of 3 tried` : 'Skipped')}>
          Continue <ArrowRight size={16} aria-hidden="true" />
        </Button>

        {doneCount === 0 && <TextLink onClick={onSkip}>Skip this</TextLink>}
      </div>
    </div>
  )
}

function TryOneCard({ def, state, onRun, onExitTo }: {
  def: CardDef; state: CardState; onRun: () => void; onExitTo: (path: string) => void
}) {
  const Glyph = def.icon
  const content = state.phase === 'done' ? <Outcome outcome={state.outcome} onExitTo={onExitTo} />
    : state.phase === 'failed' ? <Failure message={state.message} target={state.target} onExitTo={onExitTo} /> : null
  return <motion.article variants={listItemEnter} layout transition={spring.spatialFast} className="rounded-xl border border-outline/25 bg-surface-high p-m">
    <header className="grid grid-cols-[auto_minmax(0,1fr)] gap-m">
      <span className="inline-flex size-8 items-center justify-center rounded-lg bg-primary/15"><Glyph size={15} className="text-primary" aria-hidden="true" /></span>
      <div>
        <div className="flex items-baseline justify-between gap-s"><h3 className="text-[0.875rem] text-on-surface" style={fvs(550)}>{def.title}</h3>
          {state.phase === 'done' && <span className="inline-flex items-center gap-1 text-[0.75rem] text-ok"><Check size={13} aria-hidden="true" /> Done</span>}
        </div>
        <p className="mt-1 text-[0.8125rem] text-on-surface-low">{def.blurb}</p>
        {state.phase !== 'done' && <div className="mt-m"><Button variant="tonal" size="sm" loading={state.phase === 'running'} onClick={onRun}>{state.phase === 'failed' ? 'Try again' : def.action}</Button></div>}
      </div>
    </header>
    {content}
  </motion.article>
}

function Outcome({ outcome, onExitTo }: { outcome: TryOneOutcome; onExitTo: (path: string) => void }) {
  return (
    <div className="mt-3 flex flex-col gap-1.5 border-t border-outline-variant pt-3">
      <p className="text-[0.8125rem]" style={{ color: 'var(--color-success)' }}>{outcome.headline}</p>
      <dl className="flex flex-col gap-1">
        {outcome.facts.map((f) => (
          <div key={f.label} className="flex gap-2 text-[0.75rem]">
            <dt className="w-[5.5rem] shrink-0 text-on-surface-low">{f.label}</dt>
            <dd className="min-w-0 flex-1 break-words text-on-surface-var">{f.value}</dd>
          </div>
        ))}
      </dl>

      <TextLink size="xs" icon={ExternalLink} iconPosition="trailing" className="mt-0.5 self-start"
        onClick={() => onExitTo(outcome.href)}>
        {outcome.linkLabel}
      </TextLink>
    </div>
  )
}

function Failure({ message, target, onExitTo }: {
  message: string; target: SettingsTarget; onExitTo: (path: string) => void
}) {
  return (
    <div className="mt-3 flex flex-col gap-2 border-t border-outline-variant pt-3">
      <InlineError icon multiline>{message}</InlineError>
      <p className="text-on-surface-low text-[0.75rem]">{target.because}</p>
      <div>
        <Button variant="tonal" size="sm" onClick={() => onExitTo(target.path)}>
          {target.label}
        </Button>
      </div>
      <p className="text-on-surface-low text-[0.75rem]">
        This finishes setup and takes you there — you can come back to these any time from Discover.
      </p>
    </div>
  )
}
