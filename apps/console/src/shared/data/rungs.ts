import { FileText, ShieldQuestion, Undo2, Zap } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api, type AutonomyLadder, type AutonomyType } from './api'
import { useQuery } from './data'

export const RUNG_PRESENTATION: Record<string, { icon: LucideIcon; tone: string }> = {
  draft_only: { icon: FileText, tone: 'var(--color-on-surface-low)' },
  one_tap: { icon: ShieldQuestion, tone: 'var(--color-on-surface-var)' },
  auto_with_undo: { icon: Undo2, tone: 'var(--color-info)' },
  autonomous: { icon: Zap, tone: 'var(--color-primary)' },
}

export interface RungMeta { key: string; label: string; hint: string; icon: LucideIcon; tone: string }

function humanizeRung(key: string): string {
  return key ? key.replace(/_/g, ' ') : 'unknown'
}

export function rungMeta(key: string, ladder: AutonomyLadder | null): RungMeta {
  const wire = ladder?.rung_meta?.find((r) => r.key === key)
  const pres = RUNG_PRESENTATION[key] ?? { icon: Zap, tone: 'var(--color-on-surface-low)' }
  return { key, label: wire?.label ?? humanizeRung(key), hint: wire?.hint ?? '', ...pres }
}

export function rungReason(t: AutonomyType, ladder: AutonomyLadder | null): string {
  const meta = rungMeta(t.resolved_rung, ladder)
  const hint = meta.hint ? `${meta.hint} ` : ''
  return `${t.key} — ${hint}${t.authority}`
}

export function providerRungIndex(ladder: AutonomyLadder | null): Map<string, AutonomyType> {
  const index = new Map<string, AutonomyType>()
  for (const t of ladder?.types ?? []) for (const p of t.providers) index.set(p, t)
  return index
}

export function useAutonomyLadder() {
  const { data, error, refresh } = useQuery('autonomy:ladder', () => api.autonomyLadder(), { persist: true })
  return { ladder: data ?? null, error, refresh }
}
