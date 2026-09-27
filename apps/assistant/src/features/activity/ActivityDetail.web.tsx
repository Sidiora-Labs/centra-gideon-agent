import { useEffect, useRef, useState } from 'react'
import type { ModuleProps } from '../../shared/shell/webModules.web'
import { createShellRoute, serializeShellRoute, type ShellReturnContext } from '../../shared/shell/shellRoutes'
import { useShellTheme } from '../../shared/shell/shellTheme.web'
import { WorkspaceFrame } from '../../shared/shell/WorkspaceFrame.web'
import { readActivityDetail, type ActivityDetailKind, type ActivityDetailRead, type ActivityDetailRecord } from './activityRoutes'

const labels: Record<ActivityDetailKind, string> = {
  trigger_run: 'Trigger run', inbox_item: 'Inbox item', approval: 'Approval',
  notification: 'Notification', artifact: 'Artifact',
}

function rows(detail: ActivityDetailRecord): Array<[string, string]> {
  const record = detail.record as unknown as Record<string, unknown>
  if (detail.kind === 'trigger_run') {
    const trigger = record.trigger_source as Record<string, unknown> | undefined
    const counters = record.counters as Record<string, unknown> | undefined
    return [
      ['Run ID', String(record.run_id ?? record.id ?? detail.id)],
      ['Source', record.source === 'hook_summary' ? 'Lifecycle hook summary'
        : record.source === 'event_summary' ? 'Event trigger summary' : 'Native trigger run'],
      ['Trigger', String(trigger?.name ?? record.job_name ?? record.trigger_id ?? record.job_id ?? 'Unknown trigger')],
      ['Status', String(record.status ?? record.outcome ?? 'Unknown')],
      ['Started', String(record.started_at ?? 'Unknown')], ['Finished', String(record.finished_at ?? 'Unknown')],
      ['Summary', String(record.summary ?? record.error ?? record.reason ?? 'No summary available.')],
      ...(counters ? Object.entries(counters).map(([name, value]) => [name === 'run_count' ? 'Recorded runs' : 'Recorded fires', String(value)] as [string, string]) : []),
    ]
  }
  if (detail.kind === 'inbox_item') return [
    ['Inbox ID', detail.id], ['From', String(record.sender_name ?? 'Unknown sender')],
    ['Channel', String(record.channel_name ?? record.channel ?? 'Unknown')], ['Status', String(record.status ?? 'Unknown')],
    ['Message', String(record.message ?? '')], ['Context', String(record.context_summary ?? 'No additional context.')],
  ]
  if (detail.kind === 'approval') return [
    ['Approval ID', detail.id], ['Requested action', String(record.tool ?? 'Unknown action')],
    ['Source', String(record.source ?? 'Unknown')], ['Purpose', String(record.tool_purpose ?? 'No purpose provided.')],
    ['Conversation', String(record.session ?? 'Unknown')], ['Requested at', String(record.ts ?? 'Unknown')],
  ]
  if (detail.kind === 'notification') return [
    ['Notification ID', detail.id], ['Type', String(record.kind ?? 'Unknown')],
    ['Title', String(record.title ?? 'Notification')], ['Message', String(record.body ?? '')],
    ['Status', record.acked ? 'Acknowledged' : 'New'], ['Received', String(record.ts ?? 'Unknown')],
  ]
  return [
    ['Artifact ID', detail.id], ['Name', String(record.name ?? 'Untitled artifact')],
    ['Type', String(record.kind ?? 'Unknown')], ['Version', String(record.version ?? 'Unknown')],
    ['Updated', String(record.updated_at ?? 'Unknown')], ['Description', String(record.description ?? 'No description.')],
  ]
}

export default function ActivityDetail(props: ModuleProps) {
  const { route, scope, navigate, onReturn } = props
  const { palette } = useShellTheme()
  const kind = route.record?.kind as ActivityDetailKind | undefined
  const id = route.record?.id ?? ''
  const [attempt, setAttempt] = useState(0)
  const [loaded, setLoaded] = useState<{ key: string; result: ActivityDetailRead }>()
  const requestGeneration = useRef(0)
  const requestKey = `${scope.cacheKey}:${serializeShellRoute(route)}:${kind ?? ''}:${id}:${attempt}`
  const returnContext: ShellReturnContext | undefined = route.returnTo ?? props.returnTo
  useEffect(() => {
    const generation = ++requestGeneration.current
    const abort = new AbortController()
    setLoaded({ key: requestKey, result: { state: 'unavailable', message: 'Loading native detail.' } })
    if (!kind || !id) return () => abort.abort()
    void readActivityDetail(scope, kind, id, abort.signal).then(value => {
      if (!abort.signal.aborted && requestGeneration.current === generation) setLoaded({ key: requestKey, result: value })
    }, error => {
      if (!abort.signal.aborted && requestGeneration.current === generation) setLoaded({ key: requestKey,
        result: { state: 'unavailable', message: error instanceof Error ? error.message : 'Gideon could not load this record.' } })
    })
    return () => { requestGeneration.current++; abort.abort() }
  }, [scope, requestKey, kind, id])

  const result = loaded?.key === requestKey ? loaded.result
    : { state: 'unavailable' as const, message: 'Loading native detail.' }

  const back = () => {
    if (returnContext) navigate(createShellRoute(returnContext.destination, {
      view: returnContext.record ? 'detail' : 'list',
      record: returnContext.record, placement: returnContext.placement, sessionId: returnContext.sessionId,
    }))
    else onReturn()
  }
  const record = result.state === 'ready' ? result.value : null
  const isArtifact = record?.kind === 'artifact'
  return <WorkspaceFrame route={route} mode="full" title={kind ? labels[kind] : 'Activity detail'} onBack={back}
    actions={<button type="button" onClick={() => setAttempt(value => value + 1)} style={{ minHeight: 44,
      border: `1px solid ${palette.line}`, borderRadius: 10, padding: '8px 14px',
      background: palette.card, color: palette.text, font: 'inherit', cursor: 'pointer' }}>Reload record</button>}>
    <main data-activity-detail={kind} data-read-state={result.state}
      style={{ maxWidth: 860, margin: '0 auto', padding: '20px clamp(12px, 3vw, 28px) 48px' }}>
      {record ? <>
        <h2 style={{ margin: '0 0 20px', color: palette.text, overflowWrap: 'anywhere' }}>
          {record.kind === 'artifact' ? record.record.name : record.kind === 'inbox_item' ? record.record.message
            : record.kind === 'approval' ? record.record.tool : record.kind === 'notification'
              ? record.record.title : record.record.trigger_source?.name ?? record.record.job_name ?? 'Trigger activity'}
        </h2>
        <dl style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(230px, 100%), 1fr))', gap: 12, margin: 0 }}>
          {rows(record).map(([label, value]) => <div key={label} style={{ minWidth: 0, border: `1px solid ${palette.line}`,
            borderRadius: 12, padding: 14, background: palette.card }}>
            <dt style={{ color: palette.muted, fontSize: 12, marginBottom: 6 }}>{label}</dt>
            <dd style={{ margin: 0, color: palette.text, lineHeight: 1.5, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{value}</dd>
          </div>)}
        </dl>
        {record.kind === 'inbox_item' && record.record.thread_context?.length ? <section aria-label="Conversation context">
          <h3>Conversation context</h3>
          {record.record.thread_context.map((message, index) => <p key={`${message.ts ?? ''}-${index}`}>
            <strong>{message.sender_name ?? 'Participant'}:</strong> {message.text ?? ''}
          </p>)}
        </section> : null}
        {isArtifact && record.record.content ? <details style={{ marginTop: 18 }}>
          <summary style={{ minHeight: 44, cursor: 'pointer', color: palette.blueDark }}>View artifact content</summary>
          <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', color: palette.text,
            background: palette.card, border: `1px solid ${palette.line}`, borderRadius: 10, padding: 14 }}>
            {record.record.content}
          </pre>
        </details> : null}
      </> : <section role={result.state === 'unavailable' ? 'alert' : 'status'}>
        <h2>{result.state === 'denied' ? 'Access denied' : result.state === 'missing' ? 'Record unavailable'
          : 'Record could not be checked'}</h2>
        <p>{result.state === 'ready' ? '' : result.message}</p>
        <button type="button" onClick={back} style={{ minHeight: 44, border: `1px solid ${palette.line}`,
          borderRadius: 10, padding: '8px 14px', background: palette.card, color: palette.text,
          font: 'inherit', cursor: 'pointer' }}>Return to Activity</button>
      </section>}
    </main>
  </WorkspaceFrame>
}
