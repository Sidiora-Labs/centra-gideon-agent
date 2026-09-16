import { Bot, Cpu, type LucideIcon } from 'lucide-react'

export interface ProviderMeta { label: string; icon: LucideIcon; tone: string }
const knownProviders = [
  { match: 'claude', label: 'Claude Code', tone: 'var(--color-info)' },
  { match: 'codex', label: 'Codex', tone: 'var(--color-warn)' },
]
export function providerMeta(providerId: string): ProviderMeta {
  if (!providerId || providerId === 'native') return { label: 'Native', icon: Bot, tone: 'var(--color-primary)' }
  const known = knownProviders.find(provider => providerId.includes(provider.match))
  if (known) return { label: known.label, icon: Cpu, tone: known.tone }
  const name = providerId.startsWith('acp:') ? providerId.slice(4) : providerId
  const label = name.replace(/[-_]/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase())
  return { label, icon: Cpu, tone: 'var(--color-on-surface-low)' }
}
export const APPROVAL_MODES = [{ key: '', label: 'Default (hook-based)' }, { key: 'auto', label: 'Auto-approve all' }]
export function isReservedAgent(agent: { reserved?: boolean }): boolean {
  return agent.reserved === true
}
