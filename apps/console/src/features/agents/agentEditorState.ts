import { useEffect, useRef, useState } from 'react'
import { api, type SavedAgent } from '../../shared/data/api'

export interface AgentDraft {
  name: string; description: string; model: string; system_prompt: string; voice: string
  natural_voice: boolean; approval_mode: string; skills: string[]; tools: string[]; triggers: string[]
  default_dir: string; memory_store: string; specialty: string; route_hints: string
}
const textFields = ['name', 'description', 'model', 'system_prompt', 'voice', 'approval_mode', 'default_dir', 'memory_store', 'specialty', 'route_hints'] as const
const trimmedFields = ['description', 'default_dir', 'memory_store', 'specialty', 'route_hints'] as const
export function emptyDraft(): AgentDraft {
  return { name: '', description: '', model: '', system_prompt: '', voice: '', natural_voice: false, approval_mode: '', skills: [], tools: [], triggers: [], default_dir: '', memory_store: '', specialty: '', route_hints: '' }
}
export function toDraft(agent: SavedAgent): AgentDraft {
  const draft = emptyDraft()
  for (const field of textFields) draft[field] = agent[field] ?? ''
  for (const field of ['skills', 'tools', 'triggers'] as const) draft[field] = [...(agent[field] ?? [])]
  draft.natural_voice = Boolean(agent.natural_voice)
  return draft
}
export function draftToPayload(draft: AgentDraft): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  for (const field of Object.keys(emptyDraft()) as (keyof AgentDraft)[]) payload[field] = draft[field]
  payload.name = draft.name.trim().replace(/^-+|-+$/g, '')
  for (const field of trimmedFields) payload[field] = draft[field].trim()
  return payload
}
export function normalizeAgentName(value: string): string {
  const chunks = value.toLowerCase().split(/[^a-z0-9]+/)
  while (chunks[0] === '') chunks.shift()
  return chunks.join('-')
}
export interface AgentCapabilityOption { value: string; label: string; hint?: string; risk?: 'safe' | 'caution' | 'destructive' }
export function useAgentCapabilities() {
  const [catalog, setCatalog] = useState<{ skills: AgentCapabilityOption[]; tools: AgentCapabilityOption[]; triggers: AgentCapabilityOption[] }>({ skills: [], tools: [], triggers: [] })
  useEffect(() => {
    let current = true
    const sources = [
      api.skills().then(items => items.map(item => ({ value: item.key ?? item.name, label: item.name, hint: item.description }))),
      api.tools().then(items => items.map(item => ({ value: item.name, label: item.name, hint: item.provider, risk: item.risk_level }))),
      api.hooks().then(items => items.map(item => ({ value: item.id, label: item.name, hint: item.event }))),
    ]
    sources.forEach((request, index) => {
      const key = (['skills', 'tools', 'triggers'] as const)[index]
      request.then(options => { if (current) setCatalog(value => ({ ...value, [key]: options })) }).catch(() => {})
    })
    return () => { current = false }
  }, [])
  return catalog
}
export function useAgentWrite(identity: string) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef<object | null>(null)
  useEffect(() => { pending.current = null; setBusy(false); setError(''); return () => { pending.current = null } }, [identity])
  const write = async <Result,>(request: () => Promise<Result>, accept: (result: Result) => void, fallback = 'Save failed') => {
    if (pending.current) return
    const token = {}
    pending.current = token; setBusy(true); setError('')
    try { const result = await request(); if (pending.current === token) accept(result) }
    catch (failure) { if (pending.current === token) setError(failure instanceof Error ? failure.message : fallback) }
    finally { if (pending.current === token) { pending.current = null; setBusy(false) } }
  }
  return { busy, error, setError, write }
}
export function useAgentTriggerNames(bindings?: string[]) {
  const [names, setNames] = useState<Map<string, string> | null>(null)
  useEffect(() => {
    let current = true
    setNames(null)
    if (bindings?.length) api.hooks().then(hooks => {
      if (current) setNames(new Map(hooks.map(hook => [hook.id, hook.event ? `${hook.name} · ${hook.event}` : hook.name])))
    }).catch(() => { if (current) setNames(null) })
    return () => { current = false }
  }, [bindings])
  return names
}
export function useAgentRoutingNotes(agentName: string) {
  const [content, setContent] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [loadError, setLoadError] = useState('')
  const [revision, setRevision] = useState(0)
  const [saved, setSaved] = useState(false)
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const operation = useAgentWrite(agentName)
  useEffect(() => {
    let current = true
    setContent(null); setDraft(''); setLoadError(''); setSaved(false)
    api.agentMetadata(agentName).then(value => { if (current) { setContent(value); setDraft(value) } }).catch(failure => { if (current) setLoadError(failure instanceof Error ? failure.message : 'Could not load the routing note') })
    return () => { current = false; if (savedTimer.current) clearTimeout(savedTimer.current) }
  }, [agentName, revision])
  const dirty = content !== null && draft !== content
  const save = () => {
    if (!dirty) return
    const value = draft
    void operation.write(() => api.saveAgentMetadata(agentName, value), () => {
      setContent(value); setSaved(true)
      if (savedTimer.current) clearTimeout(savedTimer.current)
      savedTimer.current = setTimeout(() => setSaved(false), 1800)
    })
  }
  return { content, draft, setDraft, loadError, dirty, saved, save, retry: () => setRevision(value => value + 1), ...operation }
}
