import type { Artifact, ExperimentCampaign, TaskGraphData, WorkflowIntrospection, WorkflowRunStats, WorkflowTimelineRow } from '../../shared/data/api'
import { paper } from '../../shared/vendor/assistant-ui/elements/surfaces'
import { DataTable } from '../../shared/vendor/assistant-ui/elements/data-table'
import { Chart } from '../../shared/vendor/assistant-ui/elements/chart'
import { NumberTicker } from '../../shared/vendor/assistant-ui/elements/number-ticker'
import { MathBlock } from '../../shared/vendor/assistant-ui/elements/math-block'
import { SpecSheet } from '../../shared/vendor/assistant-ui/elements/spec-sheet'
import { TraceWaterfall, type TraceSpan } from '../../shared/vendor/assistant-ui/elements/trace-waterfall'
import { WebPreview } from '../../shared/vendor/assistant-ui/elements/web-preview'
import { Diagram } from '../../shared/vendor/assistant-ui/elements/diagram'
import { ActivityGraph } from '../../shared/vendor/assistant-ui/elements/activity-graph'
import { FlowGraph, type FlowNodeState } from '../../shared/vendor/assistant-ui/elements/flow-graph'
import { ComparisonCard } from '../../shared/vendor/assistant-ui/elements/comparison-card'
import { Timeline } from '../../shared/vendor/assistant-ui/elements/timeline'
import { JobProgress } from '../../shared/vendor/assistant-ui/elements/job-progress'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

type Cell = string | number | boolean | null
type Row = Record<string, Cell>

function isCell(value: unknown): value is Cell {
  return value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
}

export function artifactTableRows(artifact: Artifact): Row[] | null {
  if (artifact.kind !== 'json' || !artifact.content) return null
  let parsed: unknown
  try { parsed = JSON.parse(artifact.content) } catch { return null }
  if (!Array.isArray(parsed)) return null
  if (!parsed.every((entry) => entry !== null && typeof entry === 'object' && !Array.isArray(entry)
    && Object.values(entry).every(isCell))) return null
  return parsed as Row[]
}

export function StructuredArtifactTable({ artifact, onOpen, showTitle = true }: { artifact: Artifact; onOpen?: (slug: string) => void; showTitle?: boolean }) {
  const rows = artifactTableRows(artifact)
  if (!rows) return <p data-slot="data-table" className="text-sm text-on-surface-low">No tabular JSON in {artifact.name}.</p>
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row))))
  return <section aria-label={`Data table from ${artifact.name}`} className="space-y-2">
    {(showTitle || onOpen) && <div className="flex items-center justify-between gap-2">
      {showTitle && <strong>{artifact.name}</strong>}
      {onOpen && <button type="button" onClick={() => onOpen(artifact.slug)} className="underline">Open artifact</button>}
    </div>}
    <DataTable rows={rows} columns={columns.map(key => ({ key, label: key }))} />
  </section>
}

export function ArtifactMetricChart({ artifact, column }: { artifact: Artifact; column: string }) {
  const rows = artifactTableRows(artifact)
  const points = rows?.map((row) => row[column])
  if (!points?.length || !points.every((value): value is number => typeof value === 'number' && Number.isFinite(value))) {
    return <p className="text-sm text-on-surface-low">No numeric {column} series in {artifact.name}.</p>
  }
  return <div data-slot="artifact-chart" className="space-y-2">
    <NumberTicker label={`${artifact.name}: ${column}`} value={points.at(-1)!} />
    <Chart label={column} value={String(points.at(-1))} points={points} visibleCount={points.length} variant="line" />
  </div>
}

export function ArtifactWebPreview({ artifact }: { artifact: Artifact }) {
  if (artifact.kind !== 'html') return null
  const src = `/api/artifacts/${encodeURIComponent(artifact.slug)}/raw?version=${artifact.version}`
  return <WebPreview origin={src} loading={false}>
    <iframe title={artifact.name} src={src} sandbox="" referrerPolicy="no-referrer" className="h-60 w-full border-0" />
  </WebPreview>
}

export function ArtifactDiagram({ artifact }: { artifact: Artifact }) {
  if (artifact.kind !== 'svg') return null
  const src = `/api/artifacts/${encodeURIComponent(artifact.slug)}/raw?version=${artifact.version}`
  return <Diagram title={artifact.name} zoom={1}>
    <img src={src} alt={artifact.name} className="max-h-80 max-w-full object-contain" />
  </Diagram>
}

export function TaskFlowGraph({ graph, onOpen }: { graph: TaskGraphData; onOpen?: (id: string) => void }) {
  const state = (status: string): FlowNodeState => {
    const value = status.toLowerCase()
    if (['done', 'completed', 'succeeded'].includes(value)) return 'done'
    if (['running', 'in_progress', 'active'].includes(value)) return 'active'
    return 'pending'
  }
  return <FlowGraph aria-label="Task dependency graph" nodes={graph.tasks.map((task, index) => ({
    id: task.id, label: task.title, status: task.status, column: index, row: 0, state: state(task.status),
  }))} edges={graph.edges.map(({ from, to }) => ({ from, to }))}
    visibleCount={graph.tasks.length} onSelect={onOpen} />
}

export function WorkflowActivityGraph({ workflow }: { workflow: WorkflowIntrospection }) {
  const dates = workflow.timeline.map((row) => new Date(row.ts)).filter((date) => Number.isFinite(date.getTime()))
  const counts = new Map<string, number>()
  dates.forEach((date) => { const day = date.toISOString().slice(0, 10); counts.set(day, (counts.get(day) || 0) + 1) })
  if (!dates.length) return <p data-slot="activity-graph">No timestamped activity.</p>
  return <ActivityGraph title={workflow.workflow} total={`${dates.length} timestamped events`}
    data={Array.from(counts, ([day, count]) => ({ date: day, count }))}
    start={new Date(Math.min(...dates.map((date) => date.getTime())))}
    end={new Date(Math.max(...dates.map((date) => date.getTime())))} />
}

export interface ArtifactMathResult { artifact: Artifact; expression: string; note?: string }

export function ArtifactMath({ result }: { result: ArtifactMathResult }) {
  if (!result.expression.trim()) return null
  return <section data-slot="math" aria-label={`Math from ${result.artifact.name}`}>
    <MathBlock label={result.artifact.name} steps={[{ expression: result.expression, note: result.note }]} visibleSteps={1} />
  </section>
}

export function ArtifactSpecSheet({ artifact }: { artifact: Artifact }) {
  if (artifact.kind !== 'json' || !artifact.content) return null
  let value: unknown
  try { value = JSON.parse(artifact.content) } catch { return null }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const rows = Object.entries(value).filter(([, entry]) => isCell(entry)).map(([label, entry]) => ({ label, value: entry == null ? '—' : String(entry) }))
  if (!rows.length) return null
  return <SpecSheet title={artifact.name} rows={rows} visibleCount={rows.length} />
}

export function ArtifactComparison({ before, after }: { before: Artifact; after: Artifact }) {
  const left = artifactTableRows(before)
  const right = artifactTableRows(after)
  if (!left || !right) return null
  const changes: { label: string; from: Cell | undefined; to: Cell | undefined }[] = []
  for (let index = 0; index < Math.max(left.length, right.length); index++) {
    const columns = new Set([...Object.keys(left[index] || {}), ...Object.keys(right[index] || {})])
    for (const column of columns) {
      const from = left[index]?.[column]
      const to = right[index]?.[column]
      if (from !== to) changes.push({ label: `Row ${index + 1}, ${column}`, from, to })
    }
  }
  const trait = (label: string, value: Cell | undefined): string | false => value === undefined
    ? false : `${label}: ${value === '' ? 'empty string' : String(value)}`
  return <section data-slot="comparison" aria-label={`Compare ${before.name} and ${after.name}`}>
    <ComparisonCard traitLabels={changes.map((change) => change.label)} options={[
      { id: before.slug + ':' + before.version, name: before.name, headline: `${left.length} rows · version ${before.version}`,
        traits: changes.map((change) => trait(change.label, change.from)) },
      { id: after.slug + ':' + after.version, name: after.name, headline: `${right.length} rows · version ${after.version}`,
        traits: changes.map((change) => trait(change.label, change.to)) },
    ]} />
  </section>
}

export function WorkflowTimeline({ rows }: { rows: readonly WorkflowTimelineRow[] }) {
  return <Timeline aria-label="Workflow events" events={rows.map((row, index) => ({
    id: `${row.node_id}:${row.ts}:${index}`, when: row.state === 'running' ? 'now' : 'past',
    time: row.ts, title: `${row.node_id} · ${row.state}`, detail: row.detail || undefined,
  }))} visibleCount={rows.length} className="max-w-none" />
}

export function WorkflowJobProgress({ workflow }: { workflow: WorkflowIntrospection }) {
  const stats = workflow.stats
  return <JobProgress aria-label={`Progress of ${workflow.workflow}`} title={workflow.workflow}
    stages={[]} stageIndex={0} stageProgress={0}
    measured={{ completed: workflow.proof.verified_steps, total: workflow.proof.total_steps }}
    details={[
      `${workflow.proof.verified_steps}/${workflow.proof.total_steps} steps verified`,
      `${stats.steps_completed} completed · ${stats.steps_failed} failed · ${stats.unverified_steps} unverified`,
      `Run ${stats.run_id}`,
    ]} />
}

export interface RecordedSpan {
  id: string
  name: string
  parentId?: string | null
  startedAtMs: number
  endedAtMs: number | null
  status: 'running' | 'completed' | 'failed'
}

export function RunTraceWaterfall({ spans }: { spans: readonly RecordedSpan[] | null }) {
  const valid = spans?.filter((span) => Number.isFinite(span.startedAtMs)
    && (span.endedAtMs === null || (Number.isFinite(span.endedAtMs) && span.endedAtMs >= span.startedAtMs))) || []
  if (!valid.length) return <p data-slot="trace-waterfall" className="text-sm text-on-surface-low">Span timing unavailable.</p>
  const origin = Math.min(...valid.map((span) => span.startedAtMs))
  const end = Math.max(...valid.map((span) => span.endedAtMs ?? span.startedAtMs))
  const ids = new Map(valid.map((span) => [span.id, span]))
  const depth = (span: RecordedSpan): number => {
    let level = 0
    let parent = span.parentId ? ids.get(span.parentId) : undefined
    const visited = new Set([span.id])
    while (parent && !visited.has(parent.id)) { level++; visited.add(parent.id); parent = parent.parentId ? ids.get(parent.parentId) : undefined }
    return level
  }
  const rendered: TraceSpan[] = valid.map((span) => ({ id: span.id, name: span.name, depth: depth(span),
    startMs: span.startedAtMs - origin, durationMs: (span.endedAtMs ?? span.startedAtMs) - span.startedAtMs, status: span.status }))
  return <TraceWaterfall spans={rendered} totalMs={end - origin} visibleCount={rendered.length} />
}

export function WorkflowCostMeter({ stats }: { stats: WorkflowRunStats }) {
  return <section data-slot="cost-meter" aria-label={`Cost for run ${stats.run_id}`} className={`${paper} rounded-xl p-3`}>
    <strong>{stats.priced ? `$${stats.cost_usd.toFixed(4)}` : 'Cost unavailable'}</strong>
    <span className="ml-2 text-sm text-on-surface-low">Run {stats.run_id}</span>
    <p className="text-sm">{stats.tokens_recorded && stats.tokens !== null ? `${stats.tokens} tokens` : 'Token usage unavailable'}</p>
  </section>
}

export interface KnownQuota { used: number | null; limit: number | null; unit: string; resetsAt?: string | null }

export function MeasuredQuotaBanner({ quota }: { quota: KnownQuota | null }) {
  if (!quota || quota.used === null || quota.limit === null || quota.limit <= 0) {
    return <section data-slot="quota-banner" aria-label="Quota" className={`${paper} rounded-xl p-3`}>Quota unavailable</section>
  }
  return <section data-slot="quota-banner" aria-label="Quota" className={`${paper} rounded-xl p-3`}>
    <strong>{Math.max(0, quota.limit - quota.used)} {quota.unit} remaining</strong>
    <meter min={0} max={quota.limit} value={Math.min(quota.limit, Math.max(0, quota.used))} aria-label={`${quota.unit} used`} />
    {quota.resetsAt && <time dateTime={quota.resetsAt}>Resets {quota.resetsAt}</time>}
  </section>
}

export function ExperimentScoreBreakdown({ campaign }: { campaign: ExperimentCampaign }) {
  return <section data-slot="score-breakdown" aria-label={`Scores for ${campaign.title}`} className={`${paper} rounded-xl p-3`}>
    <h3>{campaign.title}</h3>
    <p className="text-sm">Metric: {campaign.metric} · {campaign.direction}</p>
    <ol>{campaign.attempts.map((attempt) => <li key={attempt.ordinal}>
      Attempt {attempt.ordinal}: {attempt.score === null ? 'Score unavailable' : attempt.score}
      {attempt.valid === false && <span> · invalid</span>}
    </li>)}</ol>
  </section>
}

export function RecordedArtifactImage({ artifact }: { artifact: Artifact }) {
  if (artifact.kind !== 'image') return null
  return <figure data-slot="image" className={`${paper} rounded-xl p-3`}>
    <img src={`/api/artifacts/${encodeURIComponent(artifact.slug)}/raw?version=${artifact.version}`}
      alt={artifact.name} loading="lazy" className="max-h-80 max-w-full object-contain" />
    <figcaption className="text-xs text-on-surface-low">{artifact.name} · version {artifact.version}</figcaption>
  </figure>
}

export function ArtifactFile({ artifact }: { artifact: Artifact }) {
  const href = `/api/artifacts/${encodeURIComponent(artifact.slug)}/raw?version=${artifact.version}`
  return <article data-slot="file" className={`${paper} rounded-xl p-3`}>
    <strong>{artifact.name}</strong>
    <p className="text-xs text-on-surface-low">{artifact.kind} · version {artifact.version}</p>
    <a href={href} download={artifact.name} className="text-sm underline">Download file</a>
  </article>
}

export function ArtifactMarkdownText({ artifact }: { artifact: Artifact }) {
  if (artifact.kind !== 'markdown' || artifact.content == null) return null
  return <article data-slot="markdown-text" className={`${paper} prose rounded-xl p-3`}>
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{artifact.content}</ReactMarkdown>
  </article>
}

export function ArtifactCodeText({ artifact }: { artifact: Artifact }) {
  if (artifact.kind !== 'text' || artifact.content == null) return null
  return <pre data-slot="code-text" className={`${paper} overflow-x-auto rounded-xl p-3`}><code>{artifact.content}</code></pre>
}
