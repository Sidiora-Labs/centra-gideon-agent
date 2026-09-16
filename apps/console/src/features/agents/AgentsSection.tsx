import { AgentsListPage } from './AgentsListPage'
import { AgentCreatePage } from './AgentCreatePage'
import type { RouteProps } from '../../app/shell/useQueryState'

export function AgentsSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  const destination = sub?.split('/')[0]
  const closeCreate = () => navigate('agents')
  return destination === 'new'
    ? <AgentCreatePage onBack={closeCreate} onCreated={closeCreate} />
    : <AgentsListPage key={navEpoch} query={query} setQuery={setQuery} onCreate={() => navigate('agents/new')} />
}
