import { useState } from 'react'
import { AgentStatus } from '../../shared/vendor/assistant-ui/elements/agent-status'
import { AgentCard } from '../../shared/vendor/assistant-ui/elements/agent-card'
import { BackgroundInbox } from '../../shared/vendor/assistant-ui/elements/background-inbox'
import { CheckpointHistory, type Checkpoint } from '../../shared/vendor/assistant-ui/elements/checkpoint-history'
import { ScheduleCard } from '../../shared/vendor/assistant-ui/elements/schedule-card'
import { TraceWaterfall, type TraceSpan } from '../../shared/vendor/assistant-ui/elements/trace-waterfall'
import { CostMeter, type CostLine } from '../../shared/vendor/assistant-ui/elements/cost-meter'
import { QuotaBanner } from '../../shared/vendor/assistant-ui/elements/quota-banner'
import { TaskCard } from '../../shared/vendor/assistant-ui/elements/task-card'
import { api, type McpServer, type SavedAgent, type ScheduleJob, type ScheduleRun, type SpawnedAgent, type UsageAgg } from '../../shared/data/api'

export const MCP_CONFIG_PROVENANCE = 'Gideon composition: assistant-ui elements/task-card.tsx (donor 72e404e)'

export function GideonAgentStatus({ agent, onOpen }: { agent: SavedAgent; onOpen?: (name: string) => void }) {
  const running = (agent.running_sessions ?? 0) > 0
  return <div aria-label={`Agent ${agent.name}`} className="flex items-center gap-2">
    {agent.running_sessions === undefined
      ? <span>{agent.name}: activity unavailable</span>
      : <AgentStatus state={running ? 'working' : 'waiting'}
        label={`${agent.name}: ${running ? `${agent.running_sessions} running` : 'idle'}`} trailing={null} />}
    {onOpen && <button type="button" onClick={() => onOpen(agent.name)}>Open agent</button>}
  </div>
}

export function GideonAgentCard({ agent }: { agent: SavedAgent }) {
  return <div aria-label={`Saved agent ${agent.name}`}>
    <AgentCard name={agent.name} description={agent.description ?? ''} provider={agent.provider}
      model={agent.model} skills={(agent.skills ?? []).map(name => ({ name, description: '' }))} />
  </div>
}

export function BackgroundAgentRuns({ agents, onOpen }: { agents: SpawnedAgent[]; onOpen?: (id: string) => void }) {
  return <BackgroundInbox runs={agents.map(agent => ({
    id: agent.id, title: agent.agent || agent.task || agent.id,
    state: agent.done ? agent.error ? 'failed' : 'ready' : 'running',
    elapsed: agent.elapsed == null ? 'Duration unavailable' : `${Math.round(agent.elapsed)}s`,
    summary: agent.error || agent.result,
  }))} onCollect={onOpen} />
}

export function RecordedCheckpoints({ checkpoints, currentId }: { checkpoints: Checkpoint[] | null; currentId: string }) {
  return checkpoints?.length ? <CheckpointHistory checkpoints={checkpoints} currentId={currentId} />
    : <p role="status">Checkpoint records unavailable.</p>
}

export function ScheduledAgentRun({ job, history, onSaved }: {
  job: ScheduleJob; history: ScheduleRun[]; onSaved: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const known = history.flatMap(run => {
    const status = run.status ?? ''
    if (!run.id && !run.run_id) return []
    if (!['success', 'succeeded', 'failed', 'error'].includes(status)) return []
    return [{ id: run.run_id || run.id!, at: String(run.started_at ?? 'Time unavailable'), ok: status === 'success' || status === 'succeeded' }]
  })
  async function toggle() {
    if (busy || job.read_only) return
    setBusy(true)
    setError('')
    try {
      await api.enableSchedule(job.id, !job.enabled)
      const { jobs } = await api.schedules()
      const saved = jobs.find(candidate => candidate.id === job.id)
      if (!saved || saved.enabled !== !job.enabled) throw new Error('Schedule change was not confirmed')
      onSaved()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }
  return <div aria-label={`Schedule ${job.id}`}>
    <ScheduleCard name={job.name} cadence={job.schedule} enabled={job.enabled}
      nextRun={job.next_run_ts == null ? 'Next run unavailable' : new Date(job.next_run_ts * 1000).toLocaleString()}
      history={known} onToggle={job.read_only ? undefined : () => void toggle()} />
    {history.some(run => run.error) && <ul aria-label="Schedule run errors">
      {history.filter(run => run.error).map(run => <li key={run.run_id || run.id || String(run.started_at)}>
        {run.run_id || run.id || job.id}: {run.error}
      </li>)}
    </ul>}
    {busy && <p role="status">Saving schedule…</p>}
    {error && <p role="alert">{error}</p>}
  </div>
}

export function RecordedTrace({ spans, totalMs }: { spans: TraceSpan[] | null; totalMs: number | null }) {
  return spans?.length && totalMs != null && totalMs > 0
    ? <TraceWaterfall spans={spans} totalMs={totalMs} visibleCount={spans.length} />
    : <p role="status">Structured trace spans unavailable.</p>
}

export function RecordedCost({ run, session, lines }: { run: UsageAgg | null; session: UsageAgg | null; lines: CostLine[] | null }) {
  return run?.priced && session?.priced && lines
    ? <CostMeter runCost={`$${run.cost_usd.toFixed(6)}`} sessionCost={`$${session.cost_usd.toFixed(6)}`} lines={lines} />
    : <p role="status">Priced run and session costs unavailable.</p>
}

export interface ConfiguredQuotaSummary {
  plan: { name: string; token_limit: number | null; cycle_end: string }
  usage: { tokens: number; basis: string }
  reservations?: { reserved_tokens: number }
}
export function ConfiguredQuota({ summary }: { summary: ConfiguredQuotaSummary }) {
  return summary.plan.token_limit != null && summary.plan.token_limit > 0
    ? <div aria-label={`Configured quota ${summary.plan.name}`}>
      <QuotaBanner used={summary.usage.tokens} limit={summary.plan.token_limit} unit="tokens"
        remainingLabel={summary.reservations ? 'tokens unconsumed before reservations' : undefined}
        resetsIn={summary.plan.cycle_end ? `on ${summary.plan.cycle_end}` : undefined} upgradeLabel="" />
      <p>Owner configured limit · {summary.usage.basis}</p>
    </div>
    : <p role="status">No configured token ceiling for {summary.plan.name}.</p>
}

export function McpServerResult({ server, allowManage, onSaved }: { server: McpServer; allowManage: boolean; onSaved: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function toggle() {
    if (busy || server.enabled === undefined || !allowManage) return
    setBusy(true)
    setError('')
    try {
      await api.toggleMcpServer(server.name, !server.enabled)
      const saved = (await api.mcpServers()).find(item => item.name === server.name)
      if (!saved || saved.enabled !== !server.enabled) throw new Error('MCP change was not confirmed')
      onSaved()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }
  return <div aria-label={`MCP server ${server.name}`}>
    <TaskCard label={server.name} meta={server.status} state={server.error ? 'failed' : 'waiting'}
      result={server.error || `${server.tools.length} tools`}
      actions={allowManage && server.enabled !== undefined
        ? <button type="button" disabled={busy} onClick={() => void toggle()}>{server.enabled ? 'Disable' : 'Enable'}</button>
        : <span>Server controls unavailable for this account.</span>} />
    {error && <p role="alert">{error}</p>}
  </div>
}

export function McpConfigDialog({ servers, allowManage, onSaved, onClose }: {
  servers: McpServer[]; allowManage: boolean; onSaved: () => void; onClose: () => void
}) {
  return <div role="dialog" aria-label="MCP configuration">
    <header className="flex items-center justify-between gap-2">
      <h2>MCP configuration</h2>
      <button type="button" onClick={onClose}>Close</button>
    </header>
    {servers.length ? servers.map(server => <McpServerResult key={server.name} server={server} allowManage={allowManage} onSaved={onSaved} />)
      : <p role="status">No MCP servers are configured.</p>}
  </div>
}
