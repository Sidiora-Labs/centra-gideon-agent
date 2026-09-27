import { useEffect, useMemo, useRef, useState } from 'react'
import type { OwnerScope } from '../../shared/auth.web'
import type { ShellReturnContext, ShellRoute } from '../../shared/shell/shellRoutes'
import { WorkspaceFrame } from '../../shared/shell/WorkspaceFrame.web'
import { useShellTheme } from '../../shared/shell/shellTheme.web'
import { ACTIVITY_SOURCES, type ActivityReadSource, type ActivitySnapshot, type ActivitySourceState } from './readActivity'
import { useActivity } from './useActivity'
import { ActivityCard, type ActivityCardItem } from './ActivityCard'
import { ACTIVITY_SOURCE_LABELS, ActivityFilters, type ActivityView } from './ActivityFilters'
import type { ActivityEntry } from './types'

type Lane = Exclude<ActivityView, 'all'>
const LANES: readonly { id: Lane; title: string }[] = [
  { id: 'attention', title: 'Needs you' }, { id: 'working', title: 'Working' },
  { id: 'finished', title: 'Finished' }, { id: 'updates', title: 'Updates' },
]

export function collapseActivityCards(entries: readonly ActivityEntry[]): readonly ActivityCardItem[] {
  const approvals = new Set(entries.filter(entry => entry.identity.sourceKind === 'approval')
    .map(entry => entry.identity.sourceId))
  const mirrors = new Map<string, ActivityEntry>()
  for (const entry of entries) {
    if (entry.identity.sourceKind === 'inbox_item' && entry.related.approvalId
      && approvals.has(entry.related.approvalId)) {
      mirrors.set(entry.related.approvalId, entry)
    }
  }
  return entries.flatMap(entry => {
    if (entry.identity.sourceKind === 'inbox_item' && entry.related.approvalId
      && approvals.has(entry.related.approvalId)) return []
    const mirror = entry.identity.sourceKind === 'approval' ? mirrors.get(entry.identity.sourceId) : undefined
    return [{ entry: mirror ? { ...entry, related: { ...entry.related,
      inboxItemId: mirror.identity.sourceId as ActivityEntry['related']['inboxItemId'] } } : entry,
      mirrorSource: mirror ? 'inbox_item' as const : undefined }]
  })
}

export function activityLane(entry: ActivityEntry): Lane {
  if (entry.status.outcome === 'waiting_approval' || entry.status.outcome === 'waiting_input') return 'attention'
  if (['working', 'queued', 'paused', 'blocked', 'stopping', 'escalated'].includes(entry.status.outcome)) return 'working'
  if (['complete', 'failed', 'cancelled', 'skipped', 'sent', 'handled', 'dismissed',
    'filtered', 'acknowledged', 'available'].includes(entry.status.outcome)) return 'finished'
  return 'updates'
}

function sourceDescription(state: ActivitySourceState): string {
  const count = state.entries.length
  const suffix = state.coverage === 'missing_ids' ? ` ${state.omittedWithoutId} record(s) lacked a native ID.`
    : state.coverage === 'bounded' ? ' Results are bounded.' : ''
  if (state.phase === 'idle') return 'Not read yet.'
  if (state.phase === 'loading') return count ? `Refreshing; ${count} saved record(s) are stale.` : 'Loading.'
  if (state.phase === 'empty') return `Empty after a successful read.${suffix}`
  if (state.phase === 'ready') return `${count} current record(s).${suffix}`
  if (state.phase === 'denied') return 'Access denied.'
  if (state.phase === 'unavailable') return state.coverage === 'no_read_route'
    ? 'No read route is available yet.' : 'Source unavailable.'
  return count ? `Read failed; ${count} saved record(s) are stale.` : 'Read failed.'
}

function ActivityHealth({ snapshot, refresh, loadMore }: {
  snapshot: ActivitySnapshot
  refresh: () => Promise<void>
  loadMore: (source: ActivityReadSource) => Promise<void>
}) {
  const { palette } = useShellTheme()
  return <section aria-label="Activity source health" style={{ marginTop: 24, borderTop: `1px solid ${palette.line}`,
    paddingTop: 16 }}>
    <h2 style={{ fontSize: 16, margin: '0 0 10px' }}>Source status</h2>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(260px, 100%), 1fr))', gap: 8 }}>
      {ACTIVITY_SOURCES.map(source => {
        const state = snapshot.sources[source]
        return <div key={source} data-source-health={source} data-phase={state.phase} data-freshness={state.freshness}
          style={{ border: `1px solid ${palette.line}`, borderRadius: 10, padding: '10px 12px',
            background: palette.card, minWidth: 0 }}>
          <strong style={{ fontSize: 13 }}>{ACTIVITY_SOURCE_LABELS[source]}</strong>
          <p style={{ margin: '4px 0 0', color: palette.muted, fontSize: 13, lineHeight: 1.45 }}>
            {sourceDescription(state)}
          </p>
          {state.error && state.phase !== 'unavailable' && <p role="alert" style={{ margin: '4px 0 0',
            fontSize: 12, overflowWrap: 'anywhere', color: palette.danger }}>{state.error}</p>}
          {(state.phase === 'failed' || state.phase === 'unavailable' || state.phase === 'denied') &&
            <button type="button" onClick={() => void refresh()} style={{ minHeight: 44,
              border: 0, padding: '8px 0', background: 'transparent', color: palette.blueDark,
              font: 'inherit', cursor: 'pointer' }}>Retry sources</button>}
          {state.nextOffset !== null && <button type="button" onClick={() => void loadMore(source)}
            style={{ minHeight: 44, border: 0, padding: '8px 0', background: 'transparent',
              color: palette.blueDark, font: 'inherit', cursor: 'pointer' }}>Load more {ACTIVITY_SOURCE_LABELS[source]}</button>}
        </div>
      })}
    </div>
  </section>
}

export type ActivityScreenProps = Readonly<{
  route: ShellRoute
  scope: OwnerScope
  navigate: (route: ShellRoute) => void
  returnTo?: ShellReturnContext
  onReturn: () => void
}>

export default function ActivityScreen(props: ActivityScreenProps) {
  return <ActivityScreenBody key={`${props.scope.cacheKey}:${props.route.placement?.query?.view ?? ''}:${props.route.placement?.query?.source ?? ''}:${props.route.placement?.query?.selected ?? ''}`} {...props} />
}

function ActivityScreenBody({ route, scope, navigate, returnTo, onReturn }: ActivityScreenProps) {
  const { palette } = useShellTheme()
  const { snapshot, refresh, loadMore } = useActivity(scope)
  const query = route.placement?.query
  const savedView = query?.view as ActivityView | undefined
  const savedSource = query?.source as ActivityReadSource | 'all' | undefined
  const selectedId = query?.selected
  const savedScroll = Number(query?.scroll ?? route.returnTo?.scrollY ?? returnTo?.scrollY ?? 0)
  const restoredSelection = useRef<string | undefined>(undefined)
  const contentRef = useRef<HTMLDivElement>(null)
  const [view, setView] = useState<ActivityView>(savedView && ['all', 'attention', 'working', 'finished', 'updates'].includes(savedView) ? savedView : 'all')
  const [source, setSource] = useState<ActivityReadSource | 'all'>(savedSource && ['all', ...ACTIVITY_SOURCES].includes(savedSource) ? savedSource : 'all')
  useEffect(() => { void refresh() }, [scope.cacheKey])
  const cards = useMemo(() => collapseActivityCards(snapshot.entries), [snapshot.entries])
  const sourceCards = cards.filter(item => source === 'all' || item.entry.identity.sourceKind === source
    || item.mirrorSource === source)
  const visible = sourceCards.filter(item => view === 'all' || activityLane(item.entry) === view)
  const anyLoading = ACTIVITY_SOURCES.some(kind => snapshot.sources[kind].phase === 'loading'
    || snapshot.sources[kind].phase === 'idle')
  const anyProblem = ACTIVITY_SOURCES.some(kind => ['failed', 'denied', 'unavailable'].includes(snapshot.sources[kind].phase))
  const filtered = view !== 'all' || source !== 'all'
  useEffect(() => {
    if (!selectedId || !Number.isSafeInteger(savedScroll) || savedScroll < 0
      || restoredSelection.current === `${selectedId}:${savedScroll}`) return
    const selectedCard = Array.from(contentRef.current?.querySelectorAll<HTMLElement>('[data-activity-id]') ?? [])
      .find(card => card.dataset.activityId === selectedId)
    if (!selectedCard) return
    restoredSelection.current = `${selectedId}:${savedScroll}`
    window.requestAnimationFrame(() => {
      const frame = contentRef.current?.closest<HTMLElement>('.gideon-workspace-frame')
        ?.querySelector<HTMLElement>('[data-workspace-scroll]')
      if (frame) frame.scrollTop = savedScroll
    })
  }, [selectedId, savedScroll, visible])

  return <WorkspaceFrame route={route} mode="full" title="Activity"
    onBack={returnTo || route.returnTo ? onReturn : undefined}
    actions={<button type="button" onClick={() => void refresh()} style={{ minHeight: 44,
      border: `1px solid ${palette.line}`, borderRadius: 10, padding: '8px 14px',
      background: palette.card, color: palette.text, font: 'inherit', cursor: 'pointer' }}>Refresh</button>}>
    <div ref={contentRef} style={{ maxWidth: 1080, margin: '0 auto', padding: '14px clamp(4px, 2vw, 16px) 40px' }}>
      <p style={{ color: palette.muted, margin: '0 0 18px', lineHeight: 1.5 }}>
        Gideon work, reviews, and results from their native records.
      </p>
      <ActivityFilters view={view} source={source} onView={setView} onSource={setSource} palette={palette} />
      <p role="status" aria-live="polite" style={{ color: palette.muted, margin: '0 0 16px' }}>
        {visible.length} {visible.length === 1 ? 'card' : 'cards'} shown{anyLoading ? '; sources are loading' : ''}.
      </p>
      {visible.length === 0 && <div role="status" data-activity-empty={filtered ? 'filtered' : anyLoading ? 'loading' : anyProblem ? 'incomplete' : 'empty'}
        style={{ border: `1px solid ${palette.line}`, borderRadius: 14, padding: 20,
          background: palette.card, color: palette.muted, lineHeight: 1.5 }}>
        {filtered ? 'No records match these filters. Clear a filter to see other Activity.'
          : anyLoading ? 'Loading Activity from Gideon sources.'
            : anyProblem ? 'No records are available from the healthy sources. Check source status and retry.'
              : 'No Activity records yet. New work and results will appear here.'}
      </div>}
      {(view === 'all' ? LANES : LANES.filter(lane => lane.id === view)).map(lane => {
        const items = visible.filter(item => activityLane(item.entry) === lane.id)
        if (!items.length) return null
        return <section key={lane.id} data-activity-lane={lane.id} aria-label={lane.title}
          style={{ marginBottom: 28 }}>
          <h2 style={{ color: palette.text, fontSize: 18, margin: '0 0 12px' }}>
            {lane.title} <span style={{ color: palette.muted, fontSize: 14, fontWeight: 500 }}>({items.length})</span>
          </h2>
          <div style={{ display: 'grid', gap: 12,
            gridTemplateColumns: 'repeat(auto-fit, minmax(min(320px, 100%), 1fr))' }}>
            {items.map(item => <ActivityCard key={item.entry.identity.key} item={item} palette={palette}
              selected={item.entry.identity.sourceId === selectedId}
              onOpen={() => {
                const frame = contentRef.current?.closest<HTMLElement>('.gideon-workspace-frame')
                  ?.querySelector<HTMLElement>('[data-workspace-scroll]')
                const context = { destination: 'activity' as const, sessionId: route.sessionId ?? route.returnTo?.sessionId ?? returnTo?.sessionId,
                  placement: { id: 'activity', query: { view, source, selected: item.entry.identity.sourceId,
                    scroll: String(Math.max(0, Math.round(frame?.scrollTop ?? 0))) } } }
                navigate({ ...item.entry.destination.route, returnTo: context })
              }} />)}
          </div>
        </section>
      })}
      <ActivityHealth snapshot={snapshot} refresh={refresh} loadMore={loadMore} />
    </div>
  </WorkspaceFrame>
}
