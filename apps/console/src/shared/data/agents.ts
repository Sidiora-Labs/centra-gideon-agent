import { useEffect, useState } from 'react'
import { api, type AgentProvider, type DiscoveredAgent, type ModelItem } from './api'
import { invalidateKeys } from './data'

export const AGENT_ROUTING_MUTES_KEY = 'agents:routing-mutes'
export const canonicalAgentKey = (agent: string): string => agent.trim().toLocaleLowerCase()
export const unmuteAgent = async (agent: string): Promise<string> => {
  const result = await api.routingUnmute(agent)
  invalidateKeys(AGENT_ROUTING_MUTES_KEY)
  return result.agent
}


export async function loadAcpDiscovered(providers: AgentProvider[]): Promise<Record<string, DiscoveredAgent[]>> {
  const acp = providers.filter((p) => p.type !== 'native' && p.ready)
  const results = await Promise.allSettled(acp.map((p) => api.agentProviderAgents(p.provider_id)))
  const map: Record<string, DiscoveredAgent[]> = {}
  acp.forEach((p, i) => { const r = results[i]; if (r.status === 'fulfilled') map[p.provider_id] = r.value.agents })
  return map
}

export interface AgentOption {
  value: string
  label: string
  group: string
  description?: string
}

export function flattenAgentOptions(nativeNames: string[], discovered: Record<string, DiscoveredAgent[]>): AgentOption[] {
  const out: AgentOption[] = nativeNames.map((n) => ({ value: n, label: n, group: 'Native' }))
  for (const [providerId, agents] of Object.entries(discovered)) {
    for (const d of agents) out.push({ value: d.id, label: d.name, group: providerId, description: d.description })
  }
  return out
}

export function useAgentCatalog(opts: { native?: 'saved' | 'installed' } = {}): { options: AgentOption[]; loading: boolean; discovered: Record<string, DiscoveredAgent[]> } {
  const nativeSource = opts.native ?? 'saved'
  const [options, setOptions] = useState<AgentOption[]>([])
  const [discovered, setDiscovered] = useState<Record<string, DiscoveredAgent[]>>({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    ;(async () => {
      const [nat, providers] = await Promise.allSettled([
        nativeSource === 'installed' ? api.agentsInstalled() : api.savedAgents(),
        api.agentProviders(),
      ])
      if (!alive) return
      const nativeNames = nat.status === 'fulfilled' ? nat.value.map((a) => a.name) : []
      const disc = providers.status === 'fulfilled' ? await loadAcpDiscovered(providers.value) : {}
      if (!alive) return
      setDiscovered(disc)
      setOptions(flattenAgentOptions(nativeNames, disc))
      setLoading(false)
    })()
    return () => { alive = false }
  }, [nativeSource])

  return { options, loading, discovered }
}

export async function ensureBindableAgentName(value: string, discovered: Record<string, DiscoveredAgent[]>): Promise<string> {
  if (!value.startsWith('acp:')) return value
  let found: { providerId: string; agent: DiscoveredAgent } | null = null
  for (const [providerId, list] of Object.entries(discovered)) {
    const a = list.find((d) => d.id === value)
    if (a) { found = { providerId, agent: a }; break }
  }
  if (!found) return value
  const { providerId, agent } = found
  const profileName = `${providerId}-${agent.provider_agent || agent.name}`
    .replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64)
  const existing = await api.agents().then((d) => d.agents.find((p) => p.name === profileName)).catch(() => undefined)
  if (!existing) {
    await api.createAgent({
      name: profileName, provider: providerId, provider_agent: agent.provider_agent,
      description: `${agent.name} (${providerId})`,
    }).catch(() => {})
  }
  return profileName
}

export interface ModelOption { value: string; label: string; group: string; description?: string }

export function flattenModelOptions(models: ModelItem[]): ModelOption[] {
  return models.map((m) => ({ value: m.name, label: m.model_name || m.name, group: m.provider || 'Models', description: m.description }))
}

export function useModelCatalog(): { options: ModelOption[]; loading: boolean } {
  const [options, setOptions] = useState<ModelOption[]>([])
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    api.models().then((m) => { if (alive) { setOptions(flattenModelOptions(m)); setLoading(false) } }).catch(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])
  return { options, loading }
}

export function useActiveChatModelOptions(): { options: ModelOption[]; loading: boolean } {
  const [options, setOptions] = useState<ModelOption[]>([])
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    api.chatModels().then((rows) => {
      if (!alive) return
      setOptions(rows.map((r) => ({ value: r.name, label: r.model_id || r.name, group: r.provider || 'Models', description: r.description })))
      setLoading(false)
    }).catch(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])
  return { options, loading }
}
