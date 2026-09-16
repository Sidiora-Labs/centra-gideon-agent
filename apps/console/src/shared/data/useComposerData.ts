import { useEffect, useState } from 'react'
import { api, type AgentProvider, type DiscoveredAgent, type ModelItem, type AgentDef } from './api'
import { loadAcpDiscovered } from './agents'

export function useComposerData() {
  const [agents, setAgents] = useState<AgentDef[]>([])
  const [providers, setProviders] = useState<AgentProvider[]>([])
  const [discovered, setDiscovered] = useState<Record<string, DiscoveredAgent[]>>({})
  const [models, setModels] = useState<ModelItem[]>([])
  const [ready, setReady] = useState(false)
  const [agentsErr, setAgentsErr] = useState<unknown>(null)
  const [reloads, setReloads] = useState(0)

  useEffect(() => {
    let alive = true
    ;(async () => {
      setReady(false)
      const [ag, pr, md] = await Promise.allSettled([api.agents(), api.agentProviders(), api.models()])
      if (!alive) return
      if (ag.status === 'fulfilled') {
        setAgents(ag.value.agents.filter((a) => !a.reserved))
        setAgentsErr(null)
      } else {
        setAgentsErr(ag.reason ?? new Error('agents unavailable'))
      }
      if (md.status === 'fulfilled') setModels(md.value)
      if (pr.status === 'fulfilled') {
        setProviders(pr.value)
        const map = await loadAcpDiscovered(pr.value)
        if (alive) setDiscovered(map)
      }
      setReady(true)
    })()
    return () => { alive = false }
  }, [reloads])

  return { agents, providers, discovered, models, ready, agentsErr, retry: () => setReloads((n) => n + 1) }
}
