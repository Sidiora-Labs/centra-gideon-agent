import type { ShellPalette } from '../../shared/shell/shellTheme.web'
import { ACTIVITY_SOURCES, type ActivityReadSource } from './readActivity'

export type ActivityView = 'all' | 'attention' | 'working' | 'finished' | 'updates'

export const ACTIVITY_SOURCE_LABELS: Readonly<Record<ActivityReadSource, string>> = {
  task: 'Tasks', workflow_run: 'Workflow runs', trigger_run: 'Trigger runs',
  chat_session: 'Conversations', inbox_item: 'Inbox', approval: 'Approvals',
  notification: 'Notifications', artifact: 'Artifacts',
}

const VIEWS: readonly { id: ActivityView; label: string }[] = [
  { id: 'all', label: 'All' }, { id: 'attention', label: 'Needs you' },
  { id: 'working', label: 'Working' }, { id: 'finished', label: 'Finished' },
  { id: 'updates', label: 'Updates' },
]

export function ActivityFilters({ view, source, onView, onSource, palette }: {
  view: ActivityView
  source: ActivityReadSource | 'all'
  onView: (view: ActivityView) => void
  onSource: (source: ActivityReadSource | 'all') => void
  palette: ShellPalette
}) {
  return <div aria-label="Activity filters" style={{ display: 'grid', gap: 12, marginBottom: 20 }}>
    <div role="group" aria-label="Activity status" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      {VIEWS.map(option => <button key={option.id} type="button" aria-pressed={view === option.id}
        onClick={() => onView(option.id)} style={{ minHeight: 44, padding: '8px 14px', borderRadius: 22,
          border: `1px solid ${view === option.id ? palette.blueDark : palette.line}`,
          background: view === option.id ? palette.blue : palette.card, color: palette.text,
          font: 'inherit', fontWeight: view === option.id ? 700 : 500, cursor: 'pointer' }}>
        {option.label}
      </button>)}
    </div>
    <label style={{ display: 'grid', gap: 5, maxWidth: 320, color: palette.muted }}>
      Source
      <select value={source} onChange={event => onSource(event.target.value as ActivityReadSource | 'all')}
        style={{ minHeight: 44, border: `1px solid ${palette.line}`, borderRadius: 10,
          padding: '8px 12px', background: palette.card, color: palette.text, font: 'inherit' }}>
        <option value="all">All sources</option>
        {ACTIVITY_SOURCES.map(kind => <option key={kind} value={kind}>{ACTIVITY_SOURCE_LABELS[kind]}</option>)}
      </select>
    </label>
  </div>
}
