import type { ApprovalMode } from '../../data/api'
import type { ComposerData } from './types'

export interface ComposerChoice { value: string; label: string; hint?: string; key?: string }
export const permissionChoices: Array<ComposerChoice & { value: ApprovalMode }> = [
  { value: 'normal', label: 'Normal', hint: 'Ask before every tool' },
  { value: 'trust_reads', label: 'Trust reads', hint: 'Auto-approve read-only' },
  { value: 'trust', label: 'Trust', hint: 'Auto-approve in this chat' },
  { value: 'yolo', label: 'YOLO', hint: 'Auto-approve everywhere — auto-expires, re-enable to extend' },
]
const nativeEfforts = ['low', 'medium', 'high', 'max'].map(value => ({ value, label: value[0].toUpperCase() + value.slice(1) }))

export function discoveredChoice(data: ComposerData | undefined, agent: string) {
  for (const roster of Object.values(data?.discovered ?? {})) {
    const match = roster.find(candidate => candidate.name === agent)
    if (match) return match
  }
}

export function agentChoices(data: ComposerData | undefined, query: string) {
  const search = query.trim().toLowerCase()
  const groups: Array<{ key: string; label?: string; rows: ComposerChoice[] }> = []
  const native = data?.agents ?? []
  let count = native.length
  groups.push({ key: 'native', rows: native.filter(agent => agent.name.toLowerCase().includes(search))
    .map(agent => ({ value: agent.name, label: agent.name, key: agent.name })) })
  for (const [runtime, agents] of Object.entries(data?.discovered ?? {})) {
    count += agents.length
    const rows = agents.filter(agent => `${agent.name} ${agent.description}`.toLowerCase().includes(search) || runtime.toLowerCase().includes(search))
      .map(agent => ({ key: agent.id, value: agent.name, label: agent.name,
        hint: (agent.description ?? '').replace(/\s*--\s*⚠️[\s\S]*$/u, '').replace(/\s*\(?\[?DO NOT (UPDATE|EDIT)[\s\S]*$/i, '').trim() }))
    if (rows.length) groups.push({ key: runtime, label: runtime, rows })
  }
  const state = count > 0 ? 'ready' : data?.ready === false ? 'loading' : data?.agentsErr ? 'error' : 'empty'
  return { groups, count, search: count > 8, noMatches: !!search && groups.every(group => !group.rows.length), state }
}

export function modelChoices(data: ComposerData | undefined, agent: string, value: string) {
  const discovered = agent ? discoveredChoice(data, agent) : undefined
  const options: ComposerChoice[] = [{ value: 'Auto', label: 'Auto', hint: 'Use-case chain (Settings → Models)' }]
  if (discovered) {
    for (const model of discovered.models ?? []) options.push({ value: model, label: model, hint: discovered.runtime })
  } else {
    for (const model of data?.models ?? []) options.push({ value: model.name, label: model.model_name || model.name, hint: model.provider })
  }
  const label = !value || value === 'Auto' ? 'Auto' : data?.models.find(model => model.name === value)?.model_name || value
  return { options, label, runtimeDefault: !!discovered && options.length === 1 }
}

export function agentEfforts(data: ComposerData | undefined, agent: string) {
  const discovered = agent ? discoveredChoice(data, agent) : undefined
  return discovered ? discovered.supported_efforts ?? [] : nativeEfforts
}

export function naturalVoiceLabel(choice: '' | 'on' | 'off', effective: boolean, source: string): string {
  if (!source) return { '': 'Agent default', on: 'Plain', off: 'Off' }[choice]
  if (!effective) return 'Default'
  return source === 'agent' ? 'Plain (agent)' : 'Plain'
}

export function contextIndicator(percentage: number | undefined) {
  if (percentage === undefined || !Number.isFinite(percentage)) return null
  const value = Math.min(100, Math.max(0, percentage))
  const tone = value >= 90 ? 'danger' : value >= 70 ? 'warn' : 'primary'
  return { value, tone, remaining: 1 - value / 100, label: `Context: ${value.toFixed(0)}% used` }
}
