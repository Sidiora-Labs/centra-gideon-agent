import { useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, ChevronRight, MessageSquare, RefreshCw } from 'lucide-react'
import { api, type HypermidPrimaryContextInspectionWire, type HypermidSessionDetailWire, type HypermidSessionWire } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { ResultAnnouncement } from '../../shared/ui/ListControls'
import { SearchField } from '../../shared/ui/SearchField'
import { Surface } from '../../shared/ui/Surface'
import { StatusPill } from '../settings/bento'
import { Section } from '../settings/settingsUI'
import { adoptTimelineSnapshot, inspectionQueryKey, type HypermidTimelineSnapshot } from './hypermidState'

const SESSION_STATES = ['', 'active', 'closed', 'archived', 'unhealthy'] as const

function SessionRow({ session, active, onOpen }: { session: HypermidSessionWire; active: boolean; onOpen: () => void }) {
  const tone = session.state === 'active' ? 'ok' : session.state === 'unhealthy' ? 'warn' : 'muted'
  return <button type="button" onClick={onOpen} aria-pressed={active}
    className="flex min-h-11 w-full items-center gap-m border-b border-outline-variant/30 px-l py-m text-left last:border-0 hover:bg-surface-high focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary">
    <MessageSquare size={16} className="shrink-0 text-on-surface-low" aria-hidden />
    <span className="min-w-0 flex-1">
      <span className="block truncate text-sm text-on-surface">{session.title || 'Untitled session'}</span>
      <span data-type="caption" className="block truncate text-on-surface-low">
        {session.model_id || 'Model unknown'} · {new Date(session.updated_at).toLocaleString()}
      </span>
    </span>
    <StatusPill label={session.state} tone={tone} />
    <ChevronRight size={15} className="text-on-surface-low" aria-hidden />
  </button>
}

function asTimeline(detail: HypermidSessionDetailWire): HypermidTimelineSnapshot {
  return { session_id: detail.session.id, cursor: detail.cursor, items: detail.items, gap: detail.gap }
}

export function PrimaryContextEvidence({ inspection }: { inspection: HypermidPrimaryContextInspectionWire }) {
  const budget = inspection.budget_evidence
  const summary = inspection.summary
  return <Surface tone="container" radius="lg" className="mb-m p-m">
    <div className="flex flex-wrap items-start justify-between gap-s">
      <div className="flex min-w-0 items-start gap-s"><Activity size={16} aria-hidden className="mt-0.5 shrink-0 text-primary" />
        <div><h3 className="text-sm font-medium text-on-surface">Primary model context</h3>
          <p data-type="caption" className="mt-xs text-on-surface-low">Observed {new Date(inspection.observed_at).toLocaleString()}</p></div></div>
      <StatusPill label={inspection.state} tone={inspection.state === 'complete' ? 'ok' : inspection.state === 'stale' ? 'muted' : 'warn'} />
    </div>
    <dl className="mt-m grid gap-s text-sm sm:grid-cols-2">
      <div><dt className="text-on-surface-low">Writer</dt><dd className="text-on-surface">Hypermid · epoch {inspection.writer_status.epoch} · generation {inspection.writer_status.generation}</dd></div>
      <div><dt className="text-on-surface-low">Digest health</dt><dd className="text-on-surface">{inspection.digest_health.state} · {inspection.digest_health.component_count} components</dd></div>
      <div><dt className="text-on-surface-low">Cache</dt><dd className="text-on-surface">{inspection.cache.freshness} · {inspection.cache.bytes_known ? `${inspection.cache.bytes.toLocaleString()} bytes` : 'bytes unknown'}</dd></div>
      <div><dt className="text-on-surface-low">Recall</dt><dd className="text-on-surface">{inspection.recall_arms.length} observed arms</dd></div>
      <div><dt className="text-on-surface-low">Memory summaries</dt><dd className="text-on-surface">{summary?.selected_records == null || summary.authorized_records == null ? 'Unknown' : `${summary.selected_records} selected of ${summary.authorized_records} authorized`}</dd></div>
      <div><dt className="text-on-surface-low">Input budget</dt><dd className="text-on-surface">{budget.max_input_tokens == null ? 'Maximum unknown' : `${budget.assembled_tokens.toLocaleString()} of ${budget.max_input_tokens.toLocaleString()} tokens`} · {budget.within_limit ? 'within limit' : 'over or unknown limit'}</dd></div>
    </dl>
    {inspection.digest_health.mismatches.length > 0 && <p role="alert" className="mt-s break-words text-sm text-warn">Mismatches: {inspection.digest_health.mismatches.map((item) => item.replaceAll('_', ' ')).join(', ')}.</p>}
    {inspection.recovery_action && <p className="mt-s break-words text-sm text-on-surface"><span className="text-on-surface-low">Next action: </span>{inspection.recovery_action}</p>}
    <p data-type="caption" className="mt-s break-all font-mono text-on-surface-low">Active digest {inspection.active_digest}</p>
  </Surface>
}

function PrimaryContextStatus({ id }: { id: string }) {
  const query = useQuery(`hypermid:session:${id}:primary-context`, () => api.hypermidPrimaryContextInspection(id), { staleAfterMs: 3_000 })
  if (query.data) return <PrimaryContextEvidence inspection={query.data} />
  if (query.error) return <div className="mb-m rounded-lg bg-surface-container p-m">
    <p className="text-sm text-on-surface">Primary model context has not been observed.</p>
    <p data-type="caption" className="mt-xs text-on-surface-low">A redacted projection inspection appears after this runtime assembles a model request for the session.</p>
  </div>
  return <p role="status" className="mb-m text-sm text-on-surface-low">Loading primary model context…</p>
}

function SessionTimeline({ id }: { id: string }) {
  const query = useQuery(`hypermid:session:${id}`, () => api.hypermidSession(id), { staleAfterMs: 3_000 })
  const [timeline, setTimeline] = useState<HypermidTimelineSnapshot>()
  const [streamError, setStreamError] = useState('')
  useEffect(() => {
    if (query.data) setTimeline(asTimeline(query.data))
  }, [query.data])
  useEffect(() => {
    if (!timeline || timeline.session_id !== id || timeline.gap) return
    let stopped = false
    const poll = window.setInterval(() => {
      void api.hypermidSession(id, timeline.cursor).then((next) => {
        if (stopped) return
        setStreamError('')
        setTimeline((current) => current ? {
          ...adoptTimelineSnapshot(current, next.items),
          gap: next.gap,
        } : asTimeline(next))
      }).catch((error: unknown) => {
        if (!stopped) setStreamError(error instanceof Error ? error.message : 'Live updates are unavailable.')
      })
    }, 5_000)
    return () => { stopped = true; window.clearInterval(poll) }
  }, [id, timeline?.cursor.epoch, timeline?.cursor.sequence, timeline?.gap])

  if (!timeline && query.error) return <LoadError what="session timeline" error={query.error} onRetry={query.refresh} />
  if (!timeline) return <ListSkeleton rows={4} what="session timeline" />
  return <div className="mt-m">
    <PrimaryContextStatus id={id} />
    {(timeline.gap || streamError) && <div role="alert" className="mb-m flex items-start gap-s rounded-lg bg-warn/10 px-m py-s text-sm text-on-surface">
      <AlertTriangle size={16} className="mt-0.5 shrink-0 text-warn" aria-hidden />
      <span>{timeline.gap ? `Some activity could not be recovered: ${timeline.gap.reason}` : `Live updates paused: ${streamError}`}</span>
    </div>}
    {timeline.items.length === 0 ? <EmptyState title="No activity recorded" hint="This session has no Hypermid timeline entries yet." />
      : <ol className="relative ml-2 border-l border-outline-variant pl-l">
        {timeline.items.map((item) => <li key={`${item.cursor.epoch}:${item.cursor.sequence}`} className="relative mb-l last:mb-0">
          <span aria-hidden className="absolute -left-[1.3rem] top-1.5 size-2 rounded-full bg-primary ring-4 ring-surface" />
          <div className="flex flex-wrap items-center gap-s">
            <StatusPill label={item.kind} tone={item.kind === 'error' ? 'warn' : 'muted'} />
            <time data-type="caption" className="text-on-surface-low">{new Date(item.occurred_at).toLocaleString()}</time>
          </div>
          <p className="mt-xs whitespace-pre-wrap text-sm text-on-surface">{item.summary}</p>
          {item.trace?.trace_id && <p data-type="caption" className="mt-xs text-on-surface-low">Trace available in diagnostics</p>}
        </li>)}
      </ol>}
  </div>
}

export function SessionInspector() {
  const [text, setText] = useState('')
  const [state, setState] = useState<(typeof SESSION_STATES)[number]>('')
  const [selected, setSelected] = useState('')
  const filters = useMemo(() => ({ q: text.trim() || undefined, state: state || undefined }), [text, state])
  const sessions = useQuery(inspectionQueryKey('sessions', filters), () => api.hypermidSessions(filters))
  return <Section title="Sessions" hint="Turns, tools, memory activity, cache decisions, compaction, and errors in authoritative cursor order."
    right={<Button size="sm" variant="secondary" onClick={sessions.refresh}><RefreshCw size={14} /> Refresh</Button>}>
    <div className="mb-m grid gap-s sm:grid-cols-[minmax(0,1fr)_12rem]">
      <SearchField value={text} onChange={setText} placeholder="Search sessions" ariaLabel="Search Hypermid sessions" />
      <select value={state} onChange={(event) => setState(event.target.value as typeof state)} aria-label="Session state"
        className="min-h-11 rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary">
        {SESSION_STATES.map((value) => <option key={value || 'all'} value={value}>{value ? value.charAt(0).toUpperCase() + value.slice(1) : 'All states'}</option>)}
      </select>
    </div>
    <ResultAnnouncement count={sessions.data?.items.length ?? 0} noun="sessions" active={!!(text.trim() || state) && !sessions.revalidating && !sessions.error && !!sessions.data && sessions.data.state !== 'unavailable' && sessions.data.state !== 'unreadable'} />
    {sessions.error && !sessions.data ? <LoadError what="Hypermid sessions" error={sessions.error} onRetry={sessions.refresh} />
      : !sessions.data ? <ListSkeleton what="Hypermid sessions" />
      : sessions.data.state === 'unavailable' || sessions.data.state === 'unreadable'
        ? <LoadError what="Hypermid sessions" error={sessions.data.detail || sessions.data.state} onRetry={sessions.refresh} />
        : sessions.data.items.length === 0 ? <EmptyState title="No matching sessions" hint={text || state ? 'Adjust the filters to see other scoped sessions.' : 'Sessions will appear after Hypermid observes this project.'} />
        : <div className="grid min-w-0 gap-m lg:grid-cols-[minmax(16rem,0.85fr)_minmax(0,1.4fr)]">
          <Surface tone="container" radius="lg" className="min-w-0 overflow-hidden">
            {sessions.data.items.map((session) => <SessionRow key={session.id} session={session} active={selected === session.id} onOpen={() => setSelected(session.id)} />)}
          </Surface>
          <Surface tone="container" radius="lg" className="min-w-0 p-l">
            {selected ? <SessionTimeline id={selected} /> : <EmptyState title="Choose a session" hint="Select a session to inspect its ordered activity." />}
          </Surface>
        </div>}
    {sessions.data?.state === 'filtered' && <p data-type="caption" className="mt-s text-on-surface-low">Showing a filtered view. The complete session inventory remains cached separately.</p>}
  </Section>
}
