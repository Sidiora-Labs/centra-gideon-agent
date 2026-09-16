import { TriggersListPage } from './TriggersListPage'
import { TriggerCreatePage } from './TriggerCreatePage'
import type { RouteProps } from '../../app/shell/useQueryState'

export function TriggersSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  if ((sub || '').split('/')[0] === 'new')
    return <TriggerCreatePage onBack={() => navigate('triggers')} onCreated={() => navigate('triggers')} query={query} setQuery={setQuery} />
  return (
    <TriggersListPage
      key={navEpoch}
      onCreate={(presetId) => navigate(presetId ? `triggers/new?kind=schedule&preset=${encodeURIComponent(presetId)}` : 'triggers/new')}
      query={query}
      setQuery={setQuery}
    />
  )
}
