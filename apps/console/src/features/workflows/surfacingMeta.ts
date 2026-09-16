import { Ban, CircleDashed, Clock, Eye, Layers, Sparkles, TriangleAlert, type LucideIcon } from 'lucide-react'
import type { WorkflowSurfacingFinding, WorkflowSurfacingRow } from '../../shared/data/api'


export interface SurfacingLook { label: string; icon: LucideIcon; tone: string; hint: string }

const FRESHNESS_LOOK: Record<WorkflowSurfacingRow['freshness'], SurfacingLook> = {
  never_run: { label: 'Never run', icon: CircleDashed, tone: 'text-on-surface-low', hint: 'No completed run yet' },
  fresh: { label: 'Fresh', icon: Clock, tone: 'text-success', hint: 'Within its cadence' },
  due_soon: { label: 'Due soon', icon: Clock, tone: 'text-on-surface', hint: 'Approaching its cadence' },
  overdue: { label: 'Overdue', icon: TriangleAlert, tone: 'text-warning', hint: 'Past its cadence' },
  stale: { label: 'Stale', icon: TriangleAlert, tone: 'text-danger', hint: 'Far past its cadence' },
}

export function freshnessLook(freshness: string): SurfacingLook {
  return (
    FRESHNESS_LOOK[freshness as WorkflowSurfacingRow['freshness']] ?? {
      label: freshness || 'Unknown',
      icon: CircleDashed,
      tone: 'text-on-surface-low',
      hint: '',
    }
  )
}

const MODE_LOOK: Record<WorkflowSurfacingRow['surface_mode'], SurfacingLook> = {
  off: { label: 'Off', icon: Ban, tone: 'text-on-surface-low', hint: 'Never surfaces on its own — start it explicitly' },
  passive: { label: 'Guidance', icon: Eye, tone: 'text-on-surface', hint: 'Injects its guidance; proposes nothing' },
  suggest: { label: 'Suggests', icon: Sparkles, tone: 'text-on-surface', hint: 'May propose running itself' },
}

export function modeLook(mode: string): SurfacingLook {
  return MODE_LOOK[mode as WorkflowSurfacingRow['surface_mode']] ?? MODE_LOOK.off
}

export function tracksCadence(row: Pick<WorkflowSurfacingRow, 'cadence_days'>): boolean {
  return row.cadence_days > 0
}

export function cadenceLabel(row: Pick<WorkflowSurfacingRow, 'cadence_days' | 'escalation'>): string {
  if (row.cadence_days <= 0) return ''
  const every = row.cadence_days === 1 ? 'Every day' : `Every ${row.cadence_days} days`
  return row.escalation === 'auto' ? `${every} · files a task when overdue` : every
}

export function composerChip(
  row: Pick<WorkflowSurfacingRow, 'name' | 'surface_mode' | 'summary'>,
): { label: string; runnable: boolean; preview: string } | null {
  if (row.surface_mode === 'off') return null
  return {
    label: `SOP: ${row.name}`,
    runnable: row.surface_mode === 'suggest',
    preview: row.summary || '',
  }
}

export function findingsByDef(findings: WorkflowSurfacingFinding[]): Record<string, WorkflowSurfacingFinding[]> {
  const out: Record<string, WorkflowSurfacingFinding[]> = {}
  for (const finding of findings ?? []) {
    const key = finding?.name ?? ''
    if (!key) continue
    ;(out[key] ??= []).push(finding)
  }
  return out
}

export function packChips(row: Pick<WorkflowSurfacingRow, 'packs'>): string[] {
  return (row.packs ?? []).filter(Boolean)
}

export const PACK_ICON: LucideIcon = Layers

export function needsAttention(row: Pick<WorkflowSurfacingRow, 'overdue'>, findingCount: number): boolean {
  return row.overdue || findingCount > 0
}

export function tokenForNode(
  continuations: Array<{ node_id: string; resume_token: string; expired?: boolean }>,
  nodeId: string,
): string {
  const match = (continuations ?? []).find((c) => c.node_id === nodeId && !c.expired)
  return match?.resume_token ?? ''
}

export function canResolveNode(
  continuations: Array<{ node_id: string; resume_token: string; expired?: boolean }>,
  nodeId: string,
): boolean {
  return tokenForNode(continuations, nodeId) !== ''
}
