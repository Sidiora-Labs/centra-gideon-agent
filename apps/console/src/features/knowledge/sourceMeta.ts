import { Globe, Rss, FolderOpen, Puzzle, type LucideIcon } from 'lucide-react'


export const RAW_ENRICHMENT = 'raw'

export const HEALTH_NEEDS_RENDER = 'needs render tier'

export interface HealthMeta {
  label: string
  tone: 'ok' | 'warn' | 'danger'
  hint: string
}

export const HEALTH_META: Record<string, HealthMeta> = {
  'ok': { label: 'Healthy', tone: 'ok', hint: 'The last poll ran and this source is up to date.' },
  'degraded': { label: 'Degraded', tone: 'warn', hint: 'The last poll failed in a way the next one may recover from. The cursor was kept.' },
  'error': { label: 'Error', tone: 'danger', hint: 'The poll could not run at all — usually no provider is enrolled for this kind.' },
  'needs render tier': { label: 'Needs render tier', tone: 'warn', hint: 'This page builds its content with JavaScript, so a plain fetch sees an empty shell.' },
  'needs browse tier': { label: 'Needs browse tier', tone: 'warn', hint: 'Even a rendered fetch saw an empty shell; this page needs the full gateway browse tier, which is turned off by default.' },
}

const UNKNOWN_HEALTH: HealthMeta = {
  label: 'Unknown',
  tone: 'warn',
  hint: 'This dashboard does not recognise the status the backend reported.',
}

export function healthMeta(status: string | undefined): HealthMeta {
  return HEALTH_META[status ?? ''] ?? UNKNOWN_HEALTH
}

export const TONE_CLASS: Record<HealthMeta['tone'], string> = {
  ok: 'text-ok',
  warn: 'text-warn',
  danger: 'text-danger',
}

const FORM_ICON: Record<string, LucideIcon> = {
  web_page: Globe,
  feed: Rss,
  dir: FolderOpen,
}

export function formIcon(form: string): LucideIcon {
  return FORM_ICON[form] ?? Puzzle
}

export function fmtInterval(secs: number): string {
  if (!secs || secs < 60) return `${Math.max(0, Math.round(secs))}s`
  if (secs < 3600) return `${Math.round(secs / 60)} min`
  const hours = secs / 3600
  return `${hours % 1 === 0 ? hours : hours.toFixed(1)} hr`
}

export const INTERVAL_CHOICES = [900, 3600, 21600, 86400]

export function eventDrivenMetaLine(): string {
  return 'Indexed as artifacts change · turn off in Settings → Sources'
}
