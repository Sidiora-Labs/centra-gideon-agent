import { useState } from 'react'
import type { ShellPalette } from '../../shared/shell/shellTheme.web'
import type { ActivityEntry } from './types'
import { ACTIVITY_SOURCE_LABELS } from './ActivityFilters'

export type ActivityCardItem = Readonly<{
  entry: ActivityEntry
  mirrorSource?: 'inbox_item'
}>

function displayTime(value: ActivityEntry['occurredAt']): string {
  if (value === null) return 'Date unknown'
  const timestamp = typeof value === 'number' && value < 1e12 ? value * 1000 : value
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime()) ? 'Date unknown' : new Intl.DateTimeFormat(undefined,
    { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export function ActivityCard({ item, palette, onOpen, selected = false }: { item: ActivityCardItem; palette: ShellPalette;
  onOpen: () => void; selected?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const { entry } = item
  const source = ACTIVITY_SOURCE_LABELS[entry.identity.sourceKind]
  const status = entry.status.native ?? entry.status.outcome.replaceAll('_', ' ')
  const receipt = entry.actionability === 'none'
  return <article data-activity-key={entry.identity.key} data-activity-id={entry.identity.sourceId}
    data-source={entry.identity.sourceKind} data-selected={selected ? 'true' : 'false'}
    style={{ minWidth: 0, border: `1px solid ${palette.line}`, borderRadius: 16,
      background: palette.card, padding: '16px clamp(14px, 2vw, 20px)',
      outline: selected ? `2px solid ${palette.blueDark}` : undefined, outlineOffset: selected ? 2 : undefined,
      display: 'grid', gap: 10, boxShadow: '0 2px 10px rgba(0,0,0,.035)' }}>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 12px', justifyContent: 'space-between',
      alignItems: 'center', color: palette.muted, fontSize: 13 }}>
      <span>{source}{item.mirrorSource ? ' · Inbox' : ''}</span>
      <time dateTime={typeof entry.occurredAt === 'string' ? entry.occurredAt : undefined}>
        {displayTime(entry.occurredAt)}
      </time>
    </div>
    <div style={{ display: 'grid', gap: 5, minWidth: 0 }}>
      <strong style={{ color: palette.text, overflowWrap: 'anywhere', fontSize: 16 }}>{entry.title}</strong>
      <span style={{ color: palette.muted, fontSize: 13 }}>
        {entry.status.outcome.replaceAll('_', ' ')} · Native status: {status}
      </span>
      {entry.summary && <p style={{ margin: 0, lineHeight: 1.5, overflowWrap: 'anywhere' }}>{entry.summary}</p>}
    </div>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
      <button type="button" onClick={onOpen} aria-label={`Open ${source} detail for ${entry.title}`}
        data-open-activity-id={entry.identity.sourceId} style={{ minHeight: 44,
          border: `1px solid ${palette.line}`, borderRadius: 10, padding: '8px 13px',
          background: palette.blueDark, color: '#fff', font: 'inherit', cursor: 'pointer' }}>
        Open details
      </button>
      <button type="button" aria-expanded={expanded} aria-label={`${expanded ? 'Hide' : 'Show'} ${receipt ? 'receipt' : 'summary'} for ${entry.title}`}
        onClick={() => setExpanded(value => !value)} style={{ minHeight: 44,
          border: `1px solid ${palette.line}`, borderRadius: 10, padding: '8px 13px',
          background: palette.secondary, color: palette.text, font: 'inherit', cursor: 'pointer' }}>
        {expanded ? 'Hide summary' : receipt ? 'Show receipt' : 'Show summary'}
      </button>
      {entry.progressPercent !== null && <span style={{ color: palette.muted, fontSize: 13 }}>
        {entry.progressPercent}% complete</span>}
    </div>
    {expanded && <div role="region" aria-label={`${source} record summary`} style={{ borderTop: `1px solid ${palette.line}`,
      paddingTop: 10, display: 'grid', gap: 5, color: palette.muted, fontSize: 13, overflowWrap: 'anywhere' }}>
      <span>Source ID: {entry.identity.sourceId}</span>
      {entry.related.approvalId && <span>Approval ID: {entry.related.approvalId}</span>}
      {entry.related.inboxItemId && <span>Inbox ID: {entry.related.inboxItemId}</span>}
      {entry.latestEventId && <span>Latest event: {entry.latestEventId}</span>}
      <span>{receipt ? 'Passive update' : 'Record summary'}</span>
    </div>}
  </article>
}
