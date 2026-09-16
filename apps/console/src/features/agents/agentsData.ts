import { api, type SavedAgent, type DiscoveredAgent } from '../../shared/data/api'
import { loadAcpDiscovered } from '../../shared/data/agents'
import { useQuery, invalidateKeys } from '../../shared/data/data'

export interface NativeGroup { kind: 'native'; agents: SavedAgent[]; defaultAgent: string }
export interface DiscoveredGroup { kind: 'discovered'; providerId: string; ready: boolean; detail: string; agents: DiscoveredAgent[] }
export type AgentGroup = NativeGroup | DiscoveredGroup
export interface AgentsData { groups: AgentGroup[]; error: unknown; loaded: boolean; loading: boolean; reload: () => void }

async function fetchAgentGroups(): Promise<AgentGroup[]> {
  const [native, providers] = await Promise.all([api.agents(), api.agentProviders().catch(() => [])])
  const runtimes = providers.filter(provider => provider.type !== 'native')
  const discoveries = await loadAcpDiscovered(runtimes.filter(provider => provider.ready))
  return [
    { kind: 'native', agents: native.agents, defaultAgent: native.default_agent },
    ...runtimes.map((provider): DiscoveredGroup => ({ kind: 'discovered', providerId: provider.provider_id, ready: provider.ready, detail: provider.detail, agents: discoveries[provider.provider_id] ?? [] })),
  ]
}
export function useAgentsData(): AgentsData {
  const { data, error, loading, refresh } = useQuery('agents:groups', fetchAgentGroups, { persist: true })
  const reload = () => { invalidateKeys('agents:groups'); refresh() }
  return { groups: data ?? [], error, loaded: data !== undefined, loading, reload }
}
