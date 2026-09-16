import type { AvailableModel, HostModelFit, ModelFitVerdict } from '../../shared/data/api'
import type { StatusPillTone } from '../../shared/ui/StatusPill'

export const FIT_TONE: Record<ModelFitVerdict, StatusPillTone> = {
  green: 'ok',
  yellow: 'warn',
  red: 'danger',
  unknown: 'neutral',
}

export const FIT_LABEL: Record<ModelFitVerdict, string> = {
  green: 'Fits',
  yellow: 'Tight',
  red: "Won't fit",
  unknown: 'Fit unknown',
}

export function fitDescription(m: AvailableModel): string {
  const verdict = m.fit
  if (!verdict) return ''
  const reason = m.fit_reason
    || (m.fit_need_mb ? `needs about ${m.fit_need_mb} MB on this device` : '')
  return reason ? `${FIT_LABEL[verdict]} — ${reason}` : FIT_LABEL[verdict]
}

export function budgetKnown(host?: HostModelFit | null): boolean {
  if (!host || !host.measured) return false
  return typeof host.budget_mb === 'number' && host.budget_mb >= 0
}

export function hostFitOf(rows: AvailableModel[]): HostModelFit | undefined {
  return rows.find((r) => r.host_fit)?.host_fit
}

export function unrunnable(rows: AvailableModel[], host?: HostModelFit | null): AvailableModel[] {
  if (!budgetKnown(host)) return []
  return rows.filter((m) => m.fit === 'red')
}

export function filterByFit(
  rows: AvailableModel[], host: HostModelFit | null | undefined, hide: boolean,
): AvailableModel[] {
  if (!hide || !budgetKnown(host)) return rows
  return rows.filter((m) => m.fit !== 'red')
}

export function statedSizeMb(m: AvailableModel): { mb: number; familyMedianMb: number | null } {
  const own = Math.round(m.size_mb ?? (m.size ? m.size / 1024 / 1024 : 0))
  const quoted = m.quoted_size_mb ? Math.round(m.quoted_size_mb) : 0
  if (!own) return { mb: quoted, familyMedianMb: null }
  return { mb: own, familyMedianMb: quoted && quoted !== own ? quoted : null }
}
