import { useEffect, useRef, useState } from 'react'
import { api } from '../../shared/data/api'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { confirm } from '../../shared/ui/dialog'

export type AgentAddress = { kind: 'native'; name: string } | { kind: 'discovered'; providerId: string; id: string }
export function decodeAgentAddress(value: string): AgentAddress | null {
  const native = 'native:'
  if (value.startsWith(native)) return { kind: 'native', name: value.substring(native.length) }
  const separator = value.startsWith('acp|') ? '|' : value.startsWith('acp:') ? ':' : null
  if (!separator) return null
  const fields = value.substring(4)
  const boundary = fields.indexOf(separator)
  if (boundary < (separator === '|' ? 0 : 1)) return null
  return { kind: 'discovered', providerId: fields.substring(0, boundary), id: fields.substring(boundary + 1) }
}
export function encodeAgentAddress(address: AgentAddress | null): string | null {
  if (!address) return null
  return address.kind === 'native' ? `native:${address.name}` : ['acp', address.providerId, address.id].join('|')
}
export function agentMatcher(query: string) {
  const needle = query.trim().toLowerCase()
  return (agent: { name: string; description?: string | null }) => !needle || `${agent.name} ${agent.description ?? ''}`.toLowerCase().includes(needle)
}
export function useAgentLibraryActions(defaultAgent: string | undefined, reload: () => void) {
  const [syncing, setSyncing] = useState(false)
  const pending = useRef(new Set<string>())
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const setDefault = async (name: string) => {
    if (pending.current.has('default')) return
    pending.current.add('default')
    try {
      const accepted = await confirm({ title: `Make “${name}” the default agent?`, body: defaultAgent && defaultAgent !== name ? `New chats currently start with “${defaultAgent}”. They will start with “${name}” instead.` : `Every new chat will start with “${name}”.`, confirmLabel: 'Set default' })
      if (!accepted || !mounted.current) return
      if (await reportingWrite(`make "${name}" the default agent`, () => api.setDefaultAgent(name))) if (mounted.current) reload()
    } finally { pending.current.delete('default') }
  }
  const syncAgents = async () => {
    if (pending.current.has('sync')) return
    pending.current.add('sync'); setSyncing(true)
    try {
      const accepted = await reportingWrite('sync the agents', async () => { const response = await api.syncAgents(); if (!response?.ok) throw new Error('the gateway declined the sync') })
      if (accepted && mounted.current) reload()
    } finally { pending.current.delete('sync'); if (mounted.current) setSyncing(false) }
  }
  return { syncing, syncAgents, setDefault }
}
