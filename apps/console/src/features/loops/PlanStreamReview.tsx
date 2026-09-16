import { useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { LayoutGrid, GitBranch, Braces, Loader2 } from 'lucide-react'
import { Segmented } from '../../shared/ui/Segmented'
import { DagView } from '../tasks/DagView'
import { fvs } from '../../shared/theme/fontWeight'
import { listItemEnter } from '../../shared/theme/motion'
import { reparseBuffer, type PlanDraft } from './planStream'
import { resolvePlanNames, type PlanNames } from './planNaming'
import { layoutPlanDag } from './planGraph'

const views = [
  { key: 'cards', label: 'Proposal', icon: LayoutGrid },
  { key: 'graph', label: 'Graph', icon: GitBranch },
  { key: 'json', label: 'JSON', icon: Braces },
]
const waiting = <p data-type="body-s" className="px-m py-l text-on-surface-low">Waiting for the first step…</p>

export function PlanStreamReview({ buffer, complete, names, goal }: {
  buffer: string; complete: boolean; names?: PlanNames | null; goal?: string
}) {
  const [view, setView] = useState('cards')
  const previous = useRef<PlanDraft | null>(null)
  const draft = useMemo(() => {
    const result = reparseBuffer(buffer, previous.current, { complete })
    previous.current = result.draft
    return result.draft
  }, [buffer, complete])
  const presentation = useMemo(() => {
    const naming = resolvePlanNames(draft, names, goal ?? '')
    return { naming, graph: layoutPlanDag(draft, (step) => naming.labels[step.id] ?? step.id) }
  }, [draft, names, goal])
  const { naming, graph } = presentation
  const panels: Record<string, React.ReactNode> = {
    cards: draft.steps.length ? <ol className="grid gap-s">{draft.steps.map((step, index) => <motion.li key={step.id}
      variants={listItemEnter} initial="initial" animate="animate"
      className={`grid grid-cols-[auto_1fr] gap-x-m gap-y-1 rounded-lg border bg-surface px-m py-m ${step.pending ? 'border-primary/40' : 'border-outline-variant/30'}`}>
      <span data-type="caption" className="row-span-2 grid size-6 place-items-center rounded-md bg-surface-high tabular-nums text-on-surface-low">{index + 1}</span>
      <div className="flex items-center justify-between gap-s">
        <span data-type="label-s" style={fvs(550)} className={`min-w-0 truncate text-on-surface ${step.pending ? 'text-shimmer' : ''}`}>{naming.labels[step.id] ?? step.id}</span>
        {step.role && <span data-type="caption" className="text-on-surface-low">{step.role}</span>}
      </div>
      {step.target && <span data-type="body-s" className="text-on-surface-var">{step.target}</span>}
    </motion.li>)}</ol> : waiting,
    graph: graph.nodes.length ? <div className="overflow-auto rounded-lg border border-outline-variant/30 bg-surface-high/40 p-m"><DagView {...graph} /></div> : waiting,
    json: <pre data-type="body-s" className="overflow-x-auto whitespace-pre-wrap break-words rounded-lg border border-outline-variant/30 bg-surface-low px-m py-m font-mono leading-relaxed text-on-surface-var">{buffer.trim() || '{ }'}</pre>,
  }
  return <section className="grid gap-m">
    <header className="flex items-start justify-between gap-m">
      <div className="min-w-0 space-y-1">
        <h2 data-type="headline-s" className={`text-on-surface ${!complete && !names?.title ? 'text-shimmer' : ''}`}>{naming.title}</h2>
        <p data-type="body-s" className="text-on-surface-var">{naming.description}</p>
      </div>
      {!complete && <span data-type="caption" className="inline-flex shrink-0 items-center gap-1.5 text-on-surface-low"><Loader2 size={13} className="animate-spin text-primary" /> Planning…</span>}
    </header>
    <div className="flex flex-wrap items-center justify-between gap-s border-y border-outline-variant/20 py-s">
      <span data-type="caption" className="tabular-nums text-on-surface-low">{draft.steps.length} step{draft.steps.length === 1 ? '' : 's'}</span>
      <Segmented ariaLabel="Plan view" value={view} onChange={setView} size="sm" options={views} />
    </div>
    {panels[view]}
  </section>
}
