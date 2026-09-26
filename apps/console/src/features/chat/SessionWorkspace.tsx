import { useEffect, useMemo, useState } from 'react'
import { Activity, Bot, Boxes, GripVertical, Workflow } from 'lucide-react'
import { api, type Artifact, type SpawnControl, type SpawnedAgent, type WorkflowOutboxEntry, type WorkflowReviewPayload, type WorkflowWorkspaceReview } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'
import { ChatActivityPanel, type SidePanelData } from './ChatActivityPanel'
import { WorkflowProgressCard, workflowRefFromTool } from './WorkflowProgressCard'
import { memoryReceiptLabel, type ChatActivity, type ChatTurn, type SubagentCard } from './chatTypes'

type Pane = 'activity' | 'runs' | 'delivered' | 'agents'
const PANES: Pane[] = ['activity', 'runs', 'delivered', 'agents']
const PANE_LABELS: Record<Pane, string> = { activity: 'Files & links', runs: 'Runs', delivered: 'Delivered', agents: 'Agents' }

export function sessionRunIds(turns: readonly ChatTurn[]): string[] {
  const seen = new Set<string>()
  turns.forEach((turn) => turn.segments.forEach((segment) => {
    if (segment.kind !== 'tool') return
    const ref = workflowRefFromTool(segment.tool, segment.output)
    if (ref) seen.add(ref.runId)
  }))
  return [...seen]
}

function readOrder(key: string): Pane[] {
  try {
    const raw = JSON.parse(localStorage.getItem(key) || '[]')
    if (Array.isArray(raw)) return [...raw.filter((v): v is Pane => PANES.includes(v)), ...PANES.filter((v) => !raw.includes(v))]
  } catch { /* storage is optional */ }
  return PANES
}

function readActive(key: string): Pane {
  try { const value = localStorage.getItem(`${key}:active`); if (PANES.includes(value as Pane)) return value as Pane } catch { /* storage is optional */ }
  return 'activity'
}

function readSecondary(key: string): Pane | null {
  try { const value = localStorage.getItem(`${key}:secondary`); if (PANES.includes(value as Pane)) return value as Pane } catch { /* storage is optional */ }
  return null
}

function readSplit(key: string): number {
  try { const value = Number(localStorage.getItem(`${key}:split`)); if (value >= 25 && value <= 75) return value } catch { /* storage is optional */ }
  return 50
}

export function SessionWorkspace({ sessionKey, pane, onPane, turns, activity, onOpenFile, onOpenArtifact, subagents, onKillFanout, side }: {
  sessionKey: string
  pane: string
  onPane: (pane: Pane) => void
  turns: readonly ChatTurn[]
  activity: ChatActivity
  onOpenFile: (path: string) => void
  onOpenArtifact: (slug: string) => void
  subagents: SubagentCard[]
  onKillFanout?: () => void
  side?: SidePanelData
}) {
  const storageKey = `gideon:session-workspace:${sessionKey}`
  const [order, setOrder] = useState<Pane[]>(() => readOrder(storageKey))
  const [active, setActive] = useState<Pane>(() => PANES.includes(pane as Pane) ? pane as Pane : readActive(storageKey))
  const [secondary, setSecondary] = useState<Pane | null>(() => readSecondary(storageKey))
  const [split, setSplit] = useState(() => readSplit(storageKey))
  const [dragged, setDragged] = useState<Pane | null>(null)
  const runIds = useMemo(() => sessionRunIds(turns), [turns])

  useEffect(() => { setOrder(readOrder(storageKey)); setActive(PANES.includes(pane as Pane) ? pane as Pane : readActive(storageKey)); setSecondary(readSecondary(storageKey)); setSplit(readSplit(storageKey)) }, [storageKey])
  useEffect(() => { if (PANES.includes(pane as Pane)) setActive(pane as Pane) }, [pane])
  const choose = (next: Pane) => {
    if (next === secondary) {
      setSecondary(active)
      try { localStorage.setItem(`${storageKey}:secondary`, active) } catch { /* storage is optional */ }
    }
    setActive(next); onPane(next)
    try { localStorage.setItem(`${storageKey}:active`, next) } catch { /* storage is optional */ }
  }
  const toggleSplit = () => {
    const next = secondary ? null : order.find((item) => item !== active) ?? null
    setSecondary(next)
    try { if (next) localStorage.setItem(`${storageKey}:secondary`, next); else localStorage.removeItem(`${storageKey}:secondary`) } catch { /* storage is optional */ }
  }
  const resize = (value: number) => {
    const next = Math.max(25, Math.min(75, value))
    setSplit(next)
    try { localStorage.setItem(`${storageKey}:split`, String(next)) } catch { /* storage is optional */ }
  }
  const move = (from: Pane, to: Pane) => {
    if (from === to) return
    const next = order.filter((item) => item !== from)
    next.splice(next.indexOf(to), 0, from)
    setOrder(next)
    try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* storage is optional */ }
  }

  return <div className="flex h-full min-h-0 flex-col">
    <div role="tablist" aria-label="Session workspace panes" className="mb-m flex flex-wrap gap-xs border-b border-outline-variant/40 pb-s">
      {order.map((item) => {
        const Icon = item === 'activity' ? Activity : item === 'runs' ? Workflow : item === 'delivered' ? Boxes : Bot
        return <button key={item} type="button" role="tab" aria-selected={active === item} draggable
          onDragStart={() => setDragged(item)} onDragEnd={() => setDragged(null)}
          onDragOver={(event) => event.preventDefault()} onDrop={() => { if (dragged) move(dragged, item); setDragged(null) }}
          onClick={() => choose(item)}
          className={`inline-flex items-center gap-xs rounded-md px-s py-xs text-xs focus-visible:ring-2 focus-visible:ring-primary ${active === item ? 'bg-primary-container text-on-primary-container' : 'text-on-surface-var hover:bg-surface-high'}`}>
          <GripVertical size={11} aria-hidden="true" className="cursor-grab opacity-50" /><Icon size={13} aria-hidden="true" />{PANE_LABELS[item]}
        </button>
      })}
      <button type="button" aria-pressed={!!secondary} onClick={toggleSplit} className="rounded-md px-s py-xs text-xs text-primary hover:bg-surface-high">{secondary ? 'One pane' : 'Split panes'}</button>
    </div>
    <div className="flex min-h-0 flex-1 flex-col">
      <section role="tabpanel" aria-label={PANE_LABELS[active]} className="min-h-0 overflow-y-auto" style={{ flex: secondary ? `0 0 ${split}%` : '1 1 auto' }}>
        <PaneContent kind={active} sessionKey={sessionKey} runIds={runIds} activity={activity} onOpenFile={onOpenFile} onOpenArtifact={onOpenArtifact} subagents={subagents} onKillFanout={onKillFanout} side={side} />
      </section>
      {secondary && <>
        <div role="separator" aria-label="Resize workspace panes" aria-orientation="horizontal" tabIndex={0} aria-valuemin={25} aria-valuemax={75} aria-valuenow={split}
          onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId) }}
          onPointerMove={(event) => { if (!event.currentTarget.hasPointerCapture(event.pointerId)) return; const parent = event.currentTarget.parentElement; if (parent) resize(((event.clientY - parent.getBoundingClientRect().top) / parent.clientHeight) * 100) }}
          onPointerUp={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId) }}
          onKeyDown={(event) => { if (event.key === 'ArrowUp' || event.key === 'ArrowDown') { event.preventDefault(); resize(split + (event.key === 'ArrowUp' ? -5 : 5)) } }}
          className="my-xs h-1.5 shrink-0 cursor-ns-resize rounded-pill bg-outline-variant/50 focus-visible:bg-primary" />
        <section role="tabpanel" aria-label={PANE_LABELS[secondary]} className="min-h-0 flex-1 overflow-y-auto">
          <PaneContent kind={secondary} sessionKey={sessionKey} runIds={runIds} activity={activity} onOpenFile={onOpenFile} onOpenArtifact={onOpenArtifact} subagents={subagents} onKillFanout={onKillFanout} side={side} />
        </section>
      </>}
    </div>
  </div>
}

function PaneContent({ kind, sessionKey, runIds, activity, onOpenFile, onOpenArtifact, subagents, onKillFanout, side }: {
  kind: Pane; sessionKey: string; runIds: string[]; activity: ChatActivity; onOpenFile: (path: string) => void
  onOpenArtifact: (slug: string) => void; subagents: SubagentCard[]; onKillFanout?: () => void; side?: SidePanelData
}) {
  return <>
      {kind === 'activity' && <ChatActivityPanel activity={activity} onOpenFile={onOpenFile} subagents={subagents}
        onKillFanout={onKillFanout} side={side} />}
      {kind === 'runs' && (runIds.length ? <div className="space-y-s">{runIds.map((id) => <div key={id}><WorkflowProgressCard refObj={{ runId: id, created: false }} /><RunChanges runId={id} onOpenFile={onOpenFile} /></div>)}</div>
        : <p className="py-xl text-center text-sm text-on-surface-low">No workflow runs in this conversation.</p>)}
      {kind === 'delivered' && <DeliveredShelf sessionKey={sessionKey} runIds={runIds} onOpen={onOpenArtifact} />}
      {kind === 'agents' && <DelegatedAgents sessionKey={sessionKey} />}
    </>
}

function RunChanges({ runId, onOpenFile }: { runId: string; onOpenFile: (path: string) => void }) {
  const [open, setOpen] = useState(false)
  const [workspace, setWorkspace] = useState<WorkflowWorkspaceReview | null>(null)
  const [review, setReview] = useState<WorkflowReviewPayload | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open) return
    let live = true
    Promise.all([api.workflowRunWorkspace(runId), api.workflowReview(runId)])
      .then(([ws, rv]) => { if (live) { setWorkspace(ws); setReview(rv); setError('') } })
      .catch((reason) => { if (live) setError(reason instanceof Error ? reason.message : 'Could not load the run diff.') })
    return () => { live = false }
  }, [runId, open])
  return <div className="px-s pb-s">
    <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)} className="text-xs text-primary hover:underline">{open ? 'Hide files & diff' : 'Show files & diff'}</button>
    {open && <div className="mt-s rounded-lg border border-outline-variant/50 p-s">
      {error ? <p role="alert" className="text-xs text-danger">{error}</p>
        : !workspace || !review ? <p role="status" className="text-xs text-on-surface-low">Loading files and diff…</p>
          : <>
            {workspace.workspace.changed.length ? <ul className="mb-s space-y-xs">{workspace.workspace.changed.map((file) => <li key={file.path}>
              <button type="button" onClick={() => onOpenFile(file.path.startsWith('/') ? file.path : `${workspace.workspace.path}/${file.path}`)}
                className="w-full truncate text-left text-xs text-on-surface-var hover:text-primary" title={file.path}>{file.status} · {file.path}</button>
            </li>)}</ul> : <p className="mb-s text-xs text-on-surface-low">No changed files recorded for this run.</p>}
            {review.diff ? <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words bg-surface-low p-s text-[0.6875rem] text-on-surface-var">{review.diff}{review.diff_truncated ? '\n… Diff truncated; open the full run for more.' : ''}</pre>
              : <p className="text-xs text-on-surface-low">No diff available.</p>}
          </>}
    </div>}
  </div>
}

function DeliveredShelf({ sessionKey, runIds, onOpen }: { sessionKey: string; runIds: string[]; onOpen: (slug: string) => void }) {
  const [items, setItems] = useState<Array<{ slug: string; name: string; source: string }>>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let live = true
    setLoading(true)
    Promise.all([api.artifacts(), ...runIds.map((id) => api.workflowRunOutbox(id).then((v) => v.files).catch(() => [] as WorkflowOutboxEntry[]))])
      .then(([artifacts, ...outboxes]) => {
        if (!live) return
        const rows = new Map<string, { slug: string; name: string; source: string }>()
        for (const artifact of artifacts as Artifact[]) {
          if (artifact.events?.some((event) => event.session_id === sessionKey || event.session_id === `dashboard:${sessionKey}`)) rows.set(artifact.slug, { slug: artifact.slug, name: artifact.name, source: artifact.project_id ? `Chat · project ${artifact.project_id}` : 'Chat' })
        }
        outboxes.forEach((entries, index) => {
          for (const entry of entries as WorkflowOutboxEntry[]) rows.set(entry.slug, { slug: entry.slug, name: entry.artifact || entry.slug, source: `Run ${runIds[index].slice(0, 8)} · ${entry.node_id}` })
        })
        setItems([...rows.values()]); setError('')
      })
      .catch((reason) => { if (live) setError(reason instanceof Error ? reason.message : 'Could not load delivered artifacts.') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [sessionKey, runIds.join('|')])
  if (loading) return <p role="status" className="py-l text-sm text-on-surface-low">Loading delivered artifacts…</p>
  if (error) return <p role="alert" className="py-l text-sm text-danger">{error}</p>
  if (!items.length) return <p className="py-xl text-center text-sm text-on-surface-low">No delivered artifacts yet. Working files stay in Files & links.</p>
  return <ul className="space-y-xs">{items.map((item) => <li key={item.slug}>
    <button type="button" onClick={() => onOpen(item.slug)} className="w-full rounded-md px-s py-s text-left hover:bg-surface-high">
      <span className="block truncate text-sm text-on-surface">{item.name}</span>
      <span className="block truncate text-xs text-on-surface-low">{item.source}</span>
    </button>
  </li>)}</ul>
}

function DelegatedAgents({ sessionKey }: { sessionKey: string }) {
  const [agents, setAgents] = useState<SpawnedAgent[]>([])
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState<SpawnedAgent | null>(null)
  const [control, setControl] = useState<SpawnControl | null>(null)
  const [controlError, setControlError] = useState('')
  const [controlBusy, setControlBusy] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const refresh = () => api.spawnedAgents().then((all) => { setAgents(all.filter((agent) => agent.parent === sessionKey || agent.parent === `dashboard:${sessionKey}`)); setError('') })
    .catch((reason) => setError(reason instanceof Error ? reason.message : 'Could not load delegated agents.'))
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 5000); return () => window.clearInterval(timer) }, [sessionKey])
  useEffect(() => {
    if (!selected) { setDetail(null); setControl(null); return }
    let live = true
    const load = () => api.spawnedAgent(selected)
      .then((value) => { if (live) { setDetail(value); setError('') } })
      .catch((reason) => { if (live) setError(reason instanceof Error ? reason.message : 'Could not inspect this agent.') })
    void load()
    api.spawnedAgentControl(selected).then((value) => { if (live) { setControl(value); setControlError('') } })
      .catch((reason) => { if (live) { setControl(null); setControlError(reason instanceof Error ? reason.message : 'Controls unavailable for this agent.') } })
    const timer = window.setInterval(() => void load(), 5000)
    return () => { live = false; window.clearInterval(timer) }
  }, [selected])
  const cancel = async (agent: SpawnedAgent) => {
    setBusy(agent.id)
    try { await api.cancelSpawnedAgent(agent.id); await refresh() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not interrupt this agent.') }
    finally { setBusy('') }
  }
  const changeControl = async (axis: 'model' | 'effort', value: string) => {
    if (!selected || !value) return
    setControlBusy(true)
    setControlError('')
    try { setControl(await api.setSpawnedAgentControl(selected, axis, value)) }
    catch (reason) { setControlError(reason instanceof Error ? reason.message : 'The agent refused this change.') }
    finally { setControlBusy(false) }
  }
  return <div className="space-y-s">
    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    {!agents.length && !error && <p className="py-xl text-center text-sm text-on-surface-low">No delegated agents in this conversation.</p>}
    {agents.map((agent) => <article key={agent.id} className="rounded-lg border border-outline-variant/50 p-s">
      <div className="flex items-start gap-s"><div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-on-surface">{agent.agent || agent.id}</p>
        <p className="mt-xs whitespace-pre-wrap text-xs text-on-surface-var">{agent.task}</p>
        <p className="mt-xs text-xs text-on-surface-low">{agent.done ? agent.error ? 'Failed' : 'Done' : `Running${agent.started ? ` · ${Math.max(0, Math.round(Date.now() / 1000 - agent.started))}s` : ''}`}{agent.last_tool ? ` · ${agent.last_tool}` : ''}</p>
      </div><Button variant="ghost" size="xs" onClick={() => setSelected(selected === agent.id ? '' : agent.id)}>{selected === agent.id ? 'Close' : 'Inspect'}</Button>{!agent.done && <Button variant="danger" size="xs" disabled={busy === agent.id} onClick={() => void cancel(agent)}>Interrupt</Button>}</div>
      {selected === agent.id && <div className="mt-s border-t border-outline-variant/40 pt-s text-xs text-on-surface-var">
        {!detail ? <p role="status">Loading this agent…</p> : <>
          <p>Instance {detail.id}</p>
          <p>{detail.done ? detail.error ? 'Failed' : 'Complete' : `Running · ${detail.turns ?? 0} turns${detail.last_tool ? ` · ${detail.last_tool}` : ''}`}</p>
          {detail.memory_receipt && <p className="mt-xs">{memoryReceiptLabel(detail.memory_receipt)}</p>}
          {!detail.done && control && <div className="mt-s space-y-s">
            {control.models.length > 0 && <label className="flex items-center justify-between gap-s">Model
              <select aria-label="Delegated agent model" value={control.model} disabled={controlBusy}
                onChange={(event) => void changeControl('model', event.target.value)}
                className="min-w-0 max-w-[65%] rounded-md border border-outline-variant bg-surface px-s py-xs text-on-surface">
                {control.models.map((model) => <option key={model} value={model}>{model}</option>)}
              </select>
            </label>}
            {control.efforts.length > 0 && <label className="flex items-center justify-between gap-s">Effort
              <select aria-label="Delegated agent effort" value={control.effort} disabled={controlBusy}
                onChange={(event) => void changeControl('effort', event.target.value)}
                className="min-w-0 max-w-[65%] rounded-md border border-outline-variant bg-surface px-s py-xs text-on-surface">
                {control.efforts.map((effort) => <option key={effort.value} value={effort.value}>{effort.label}</option>)}
              </select>
            </label>}
            {!control.models.length && !control.efforts.length && <p>Live model and effort changes are unavailable for this agent.</p>}
          </div>}
          {!detail.done && !control && controlError && <p className="mt-s text-on-surface-low">Live controls unavailable.</p>}
          {controlError && <p role="alert" className="mt-s text-danger">{controlError}</p>}
          {controlBusy && <p role="status" className="mt-s text-on-surface-low">Applying change…</p>}
          {detail.done && detail.result && <pre className="mt-s max-h-52 overflow-auto whitespace-pre-wrap">{detail.result}</pre>}
        </>}
      </div>}
      {agent.error && <p role="alert" className="mt-s text-xs text-danger">{agent.error}</p>}
      {agent.done && agent.result && <details className="mt-s text-xs text-on-surface-var"><summary className="cursor-pointer">Result</summary><pre className="mt-xs max-h-52 overflow-auto whitespace-pre-wrap">{agent.result}</pre></details>}
    </article>)}
  </div>
}
