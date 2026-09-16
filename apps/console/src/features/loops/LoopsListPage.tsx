import { useState } from 'react'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { fvs } from '../../shared/theme/fontWeight'
import { motion } from 'framer-motion'
import { Plus, Pause, Play, Square, Trash2, ExternalLink, Filter, Repeat } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { Button } from '../../shared/ui/Button'
import { IconButton } from '../../shared/ui/IconButton'
import { FilterMenu, type FilterSectionDef } from '../../shared/ui/FilterMenu'
import { ListControls } from '../../shared/ui/ListControls'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { RowHitTarget } from '../../shared/ui/RowHitTarget'
import { SidePanel } from '../../shared/ui/SidePanel'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { FeedbackThumbs } from '../../shared/ui/FeedbackThumbs'
import { InvestigateButton } from '../../shared/ui/InvestigateButton'
import { Markdown } from '../../shared/ui/Markdown'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { ProgressRing } from '../../shared/ui/ProgressRing'
import { ContextMenu, type ContextMenuItem } from '../../shared/ui/motion'
import { spring, expr } from '../../shared/theme/motion'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { useVisiblePoll } from '../../shared/data/useVisiblePoll'
import { api, type GoalLoop } from '../../shared/data/api'
import { loopKindMeta } from '../../shared/data/loopKind'
import { loopToGoalLoop } from './goalAdapter'
import { rowSubject } from '../../shared/data/rowSubject'
import { activePhaseIndex, phaseMinCycles, phaseForCycle, hasDistinctName } from './loopPhases'
import { loopStatusLabel, loopStatusColor, loopStatusTone, effectiveLoopStatus, ACTIVE_LOOP_STATUSES, PRELAUNCH_LOOP_STATUSES, LOOP_ACTION_SOURCE_STATUSES } from '../../shared/data/loopStatus'
import { PageTitle } from '../../shared/ui/PageTitle'


const GOAL_GLYPH: Record<string, string> = {
  verifiable: '✓ verifiable', open_ended: '◐ open-ended', monitor: '∞ monitor',
}


function order(a: GoalLoop, b: GoalLoop) {
  if ((a.status === 'running') !== (b.status === 'running')) return a.status === 'running' ? -1 : 1
  return (b.started_at ?? b.created_at) - (a.started_at ?? a.created_at)
}

export function LoopsListPage({ onOpen, onCreate, query, setQuery }: { onOpen: (id: string) => void; onCreate: () => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const { data: loops, error: loopsErr, refresh } = useQuery<GoalLoop[]>('loops', () => api.uLoops().then((ls) => ls.filter((l) => l.kind !== 'code').map(loopToGoalLoop).sort(order)), { persist: false })
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [peekId, setPeekId] = useQueryParam(query, setQuery, 'peek', '')
  const peek = peekId ? (loops?.find((l) => l.id === peekId) ?? null) : null
  const [filterRaw, setFilter] = useQueryParam(query, setQuery, 'filter', 'active', { replace: true })
  const filter = filterRaw as 'all' | 'active' | 'ongoing' | 'done'

  const hasLive = (loops ?? []).some((l) => !['complete', 'stopped', 'failed'].includes(l.status))
  useVisiblePoll(refresh, hasLive ? 4000 : null)

  async function act(e: React.MouseEvent | undefined, id: string, action: 'pause' | 'resume' | 'stop') {
    e?.stopPropagation()
    if (!(await reportingWrite(`${action} this loop`, () => api.uLoopAction(id, action)))) return
    invalidateKeys('loops'); refresh()
  }

  async function del(e: React.MouseEvent | undefined, id: string) {
    e?.stopPropagation()
    if (confirmDelete !== id) { setConfirmDelete(id); window.setTimeout(() => setConfirmDelete((c) => (c === id ? null : c)), 4000); return }
    setConfirmDelete(null)
    if (!(await reportingWrite('delete this loop', () => api.deleteULoop(id)))) return
    invalidateKeys('loops'); refresh()
  }

  const DONE_ST = ['complete', 'stopped', 'failed']
  const SHEPHERDING = new Set([...ACTIVE_LOOP_STATUSES, ...PRELAUNCH_LOOP_STATUSES])
  const matchesFilter = (c: GoalLoop, f: typeof filter) =>
    f === 'all' ? true
    : f === 'active' ? SHEPHERDING.has(c.status)
    : f === 'ongoing' ? (c.max_cycles === 0 || c.granularity === 'forever' || c.goal_type === 'monitor')
    : DONE_ST.includes(c.status)
  const matches = (c: GoalLoop) => matchesFilter(c, filter)
  const count = (f: typeof filter) => (loops ?? []).filter((c) => matchesFilter(c, f)).length

  const filterSection: FilterSectionDef = {
    title: 'Show', value: filter, defaultKey: 'active',
    onChange: (k) => setFilter(k as typeof filter),
    options: [
      { key: 'active', label: 'Active', count: count('active') },
      { key: 'all', label: 'All', count: count('all') },
      { key: 'ongoing', label: 'Ongoing', count: count('ongoing') },
      { key: 'done', label: 'Done', count: count('done') },
    ],
  }

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<PageTitle>Loops</PageTitle>}
          right={
            <div className="flex items-center gap-s">
              <Button size="sm" className="h-10" onClick={onCreate}><Plus size={16} /> New loop</Button>
            </div>
          }
        />
      }
      controls={
        <ListControls results={{ count: (loops ?? []).filter(matches).length, noun: 'loops', active: filter !== 'active' }}>
          <FilterMenu sections={[filterSection]} />
        </ListControls>
      }
      panel={peek && (
        <SidePanel key={peek.id} fillHeight storeKey="loop-peek-w"
          icon={(() => { const KI = loopKindMeta((peek as { kind?: string }).kind).icon; return <KI size={18} className="text-primary" /> })()} title={peek.name || peek.goal.slice(0, 60)}
          onClose={() => setPeekId('')}>
          <LoopPeek loop={peek} onOpenFull={() => onOpen(peek.id)} />
        </SidePanel>
      )}
    >
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {loops === undefined && loopsErr ? (
            <LoadError what="loops" error={loopsErr} onRetry={refresh} />
          ) : loops === undefined ? (
            <ListSkeleton rows={6} what="loops" />
          ) : loops.length === 0 ? (
            <EmptyState
              icon={Repeat}
              title="No loops yet"
              hint="Describe a task and let an agent classify, plan, and pursue it autonomously."
              action={{ label: 'Start a loop', onClick: onCreate, icon: Plus }}
            />
          ) : loops.filter(matches).length === 0 ? (
            <EmptyState
              icon={Filter}
              title={filter === 'active' ? 'No active loops right now'
                : filter === 'ongoing' ? 'No ongoing loops'
                : filter === 'done' ? 'No finished loops yet'
                : 'No loops match this filter'}
              hint={`You have ${loops.length} loop${loops.length === 1 ? '' : 's'} — just none in this view.`}
              action={{ label: 'View all loops', onClick: () => setFilter('all') }}
            />
          ) : (
            <div className="flex flex-col gap-s">
              {loops
                .filter(matches)
                .map((c, i) => {
                const endedEarly = c.status === 'complete' && !!c.error_message
                const dispStatus = effectiveLoopStatus(c.status, c.error_message)
                const pct = c.status === 'complete' && !endedEarly
                  ? 1
                  : (c.max_cycles ? Math.min(1, c.total_cycles / c.max_cycles) : 0)
                const running = c.status === 'running'
                const shownCycle = running ? c.total_cycles + 1 : c.total_cycles
                const latest = c.findings?.length ? c.findings[c.findings.length - 1] : null
                const latestText = latest?.key_insight || latest?.summary
                const title = c.name || c.goal
                const goalEarnsItsLine = hasDistinctName(c.name, c.goal)
                const menuItems: ContextMenuItem[] = [
                  { icon: <ExternalLink size={15} />, label: 'Open', onSelect: () => setPeekId(c.id) },
                  ...(LOOP_ACTION_SOURCE_STATUSES.pause.has(c.status) ? [{ icon: <Pause size={15} />, label: 'Pause', onSelect: () => act(undefined, c.id, 'pause') }] : []),
                  ...(LOOP_ACTION_SOURCE_STATUSES.resume.has(c.status) ? [{ icon: <Play size={15} />, label: 'Resume', onSelect: () => act(undefined, c.id, 'resume') }] : []),
                  ...(LOOP_ACTION_SOURCE_STATUSES.stop.has(c.status) ? [{ icon: <Square size={15} />, label: 'Stop', onSelect: () => act(undefined, c.id, 'stop') }] : []),
                  ...(['complete', 'stopped', 'failed'].includes(c.status) ? [{ icon: <Trash2 size={15} />, label: 'Delete', danger: true, onSelect: () => del(undefined, c.id) }] : []),
                ]
                return (
                  <ContextMenu key={c.id} items={menuItems}>
                  <motion.div
                    initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: i * 0.03 }}
                    whileHover={{ y: -expr(3, 0.3), boxShadow: 'var(--shadow-lift)' }}
                    whileTap={{ scale: 1 - expr(0.008, 0.3) }}
                    onClick={() => setPeekId(c.id)}
                    tabIndex={-1}
                    className={`group relative flex items-center gap-l rounded-lg px-l py-l text-left cursor-pointer transition-colors overflow-hidden has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary ${peekId === c.id ? 'bg-surface-high ring-1 ring-primary' : 'bg-surface-container hover:bg-surface-high'}`}
                  >
                    <RowHitTarget label={rowSubject([title])} />
                    { }
                    {running && <span className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: 'var(--color-ok)' }} />}
                    <span className="shrink-0 inline-flex items-center justify-center size-10 rounded-lg" style={{ background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)' }}>
                      {(() => { const KI = loopKindMeta((c as { kind?: string }).kind).icon; return <KI size={20} className="text-primary" /> })()}
                    </span>
                    {
}
                    <div className="flex-1 min-w-0 min-h-[2.875rem]">
                      <div className="flex items-center gap-s">
                        <span className="size-1.5 rounded-pill shrink-0" style={{ background: loopStatusColor(dispStatus) }} />
                        <span data-type="title-m" className="truncate text-on-surface" style={fvs(500)}>{title}</span>
                        { }
                        {(() => { const k = (c as { kind?: string }).kind
                          const label = k === 'design' ? 'design' : k === 'general' ? 'loop' : (GOAL_GLYPH[c.goal_type] ?? c.goal_type)
                          const title = k === 'design' ? 'design loop' : k === 'general' ? 'general loop' : `${c.goal_type} goal`
                          return <span data-type="caption" className="shrink-0 rounded-pill px-1.5 h-4 inline-flex items-center uppercase tracking-wide bg-surface-high text-on-surface-low" title={title}>{label}</span> })()}
                        <span data-type="caption" className="shrink-0 text-on-surface-low">· {loopStatusLabel(dispStatus)}{(running || c.status === 'paused') && (c.max_cycles === 0 ? ` · ongoing · cycle ${shownCycle}` : ` · cycle ${shownCycle}/${c.max_cycles}`)}</span>
                      </div>
                      {(latestText || goalEarnsItsLine) && (
                        <p data-type="body-s" className="mt-1 text-on-surface-low truncate">
                          {latestText ? <span className="text-on-surface-var">↳ {latestText}</span> : c.goal}
                        </p>
                      )}
                    </div>

                    { }
                    <div className={`flex items-center gap-1 shrink-0 transition-opacity ${confirmDelete === c.id ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'}`}>
                      {LOOP_ACTION_SOURCE_STATUSES.pause.has(c.status) && <IconButton icon={Pause} label="Pause" size={34} onClick={(e) => act(e, c.id, 'pause')} />}
                      {LOOP_ACTION_SOURCE_STATUSES.resume.has(c.status) && <IconButton icon={Play} label="Resume" size={34} onClick={(e) => act(e, c.id, 'resume')} />}
                      {LOOP_ACTION_SOURCE_STATUSES.stop.has(c.status) && <IconButton icon={Square} label="Stop" size={34} onClick={(e) => act(e, c.id, 'stop')} />}
                      {['complete', 'stopped', 'failed'].includes(c.status) && (
                        <IconButton icon={Trash2} size={34} tone="danger"
                          label={confirmDelete === c.id ? 'Click again to delete' : 'Delete loop'}
                          onClick={(e) => del(e, c.id)}
                          className={confirmDelete === c.id ? 'text-danger' : undefined} />
                      )}
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0">
                      <ProgressRing pct={pct} tone={loopStatusColor(dispStatus)} label={`Cycle progress: ${shownCycle}${c.max_cycles ? ` of ${c.max_cycles}` : ''}`} />
                      {
}
                      <span data-type="caption" className="text-on-surface-low tabular-nums w-9"
                        title={`${c.findings?.length ?? 0} findings`}>
                        <span aria-hidden="true">{c.findings?.length ?? 0} fnd</span>
                        <span className="sr-only">{c.findings?.length ?? 0} findings</span>
                      </span>
                    </div>
                  </motion.div>
                  </ContextMenu>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </WorkbenchLayout>
  )
}

function LoopPeek({ loop, onOpenFull }: { loop: GoalLoop; onOpenFull: () => void }) {
  const dispStatus = effectiveLoopStatus(loop.status, loop.error_message)
  const running = loop.status === 'running'
  const kind = (loop as { kind?: string }).kind
  const shownCycle = running ? loop.total_cycles + 1 : loop.total_cycles
  const cycleLabel = loop.max_cycles === 0 ? `cycle ${shownCycle} · ongoing` : `cycle ${shownCycle}/${loop.max_cycles}`
  const latest = loop.findings?.length ? loop.findings[loop.findings.length - 1] : null
  const latestText = latest?.key_insight || latest?.summary
  return (
    <div className="flex flex-col gap-l">
      <Button onClick={onOpenFull}><ExternalLink size={15} /> Open full loop</Button>

      <div data-type="body-s" className="flex flex-wrap items-center gap-s">
        <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7" style={loopStatusTone(dispStatus)}>
          <span className="size-1.5 rounded-pill" style={{ background: loopStatusColor(dispStatus) }} /> {loopStatusLabel(dispStatus)}
        </span>
        { }
        {kind === 'design' ? <span className="text-on-surface-low">design</span>
          : kind === 'general' ? <span className="text-on-surface-low">loop</span>
          : <span className="text-on-surface-low">{GOAL_GLYPH[loop.goal_type] ?? loop.goal_type}</span>}
        {(running || loop.status === 'paused') && <span className="text-on-surface-low">· {cycleLabel}</span>}
      </div>

      <div>
        <div data-type="caption" className="text-on-surface-low uppercase tracking-wide mb-1.5">{kind === 'design' || kind === 'general' ? 'Task' : 'Goal'}</div>
        <div data-type="body-m" className="text-on-surface"><Markdown>{loop.goal}</Markdown></div>
        {loop.success_criteria && <p data-type="body-s" className="mt-2 text-on-surface-low"><span className="text-on-surface-var">Done when:</span> {loop.success_criteria}</p>}
      </div>

      {(loop.sub_goals?.length ?? 0) > 0 && (
        <div>
          <div data-type="caption" className="text-on-surface-low uppercase tracking-wide mb-1.5">Sub-goals · {loop.sub_goals.length}</div>
          <ul className="flex flex-col gap-1.5">
            {loop.sub_goals.map((s, i) => (
              <li key={i} data-type="body-s" className="flex items-start gap-s text-on-surface-var">
                <span className="mt-1.5 size-1 shrink-0 rounded-pill bg-primary" />{typeof s === 'string' ? s : JSON.stringify(s)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {(() => {
        const plan = (loop.execution_plan ?? []) as Record<string, unknown>[]
        if (!plan.length) return null
        const active = activePhaseIndex(loop.total_cycles, plan)
        const isActive = (i: number) => active >= 0 && i === active && (running || loop.status === 'paused')
        const fnd = loop.findings ?? []
        const cyclesIn = (i: number) => fnd.filter((f) => phaseForCycle(f.cycle, plan) === i).length
        return (
          <div>
            <div data-type="caption" className="text-on-surface-low uppercase tracking-wide mb-1.5">Execution plan · {plan.length} phases</div>
            <ol className="flex flex-col gap-1">
              {plan.map((p, i) => {
                const role = String(p.role || '').trim()
                const target = String(p.target || '').trim()
                const agent = String(p.agent_name || '').trim()
                const done = cyclesIn(i)
                const minC = phaseMinCycles(p)
                const count = done === 0 ? '' : done >= minC ? `${done} ${done === 1 ? 'cycle' : 'cycles'}` : `${done}/${minC}`
                return (
                  <li key={i} data-type="body-s" className={`flex items-start gap-s rounded-md px-2 py-1 -mx-2 ${isActive(i) ? 'bg-surface-high' : ''}`}>
                    <span data-type="caption" className="shrink-0 mt-0.5 inline-flex size-4 items-center justify-center rounded-pill bg-surface-high text-on-surface-low tabular-nums">{i + 1}</span>
                    <span className="flex-1 min-w-0 text-on-surface-var">
                      {role && <span className="text-on-surface" style={fvs(550)}>{role}</span>}
                      { }
                      <span className="text-on-surface-low"> · {agent || 'default worker'}</span>
                      <span>: {target || '(phase)'}</span>
                      {isActive(i) && <span data-type="caption" className="ml-1.5 text-primary uppercase tracking-wide">● active</span>}
                    </span>
                    {count && <span data-type="caption" className="shrink-0 mt-0.5 text-on-surface-low tabular-nums">{count}</span>}
                  </li>
                )
              })}
            </ol>
          </div>
        )
      })()}

      {latestText && (
        <div>
          <div className="mb-1.5 flex items-center gap-s">
            <span data-type="caption" className="text-on-surface-low uppercase tracking-wide">Latest finding · {loop.findings?.length ?? 0} total</span>
            {
}
            <FeedbackThumbs targetKind="loop_finding"
              targetId={`${loop.id}:${latest?.cycle ?? loop.findings!.length}`}
              producer={loop.feedback_producer}
              snapshot={{ key_insight: (latestText ?? '').slice(0, 200) }} />
            {
}
            <InvestigateButton kind="loop_finding" id={`${loop.id}:${latest?.cycle ?? ''}`} backLink={`#/loops/${loop.id}`} />
          </div>
          <p data-type="body-s" className="text-on-surface-var">{latestText}</p>
        </div>
      )}
    </div>
  )
}
