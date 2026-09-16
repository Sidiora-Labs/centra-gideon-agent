import { Sunrise, Newspaper, Moon, BellRing, type LucideIcon } from 'lucide-react'
import type { PresetDef } from '../../shared/ui/PresetEmptyState'
import { emptyDraft, type ScheduleDraft } from '../schedule/ScheduleForm'

export type Cadence =
  | { kind: 'daily'; hour: number; minute: number }
  | { kind: 'weekly'; weekday: number; hour: number; minute: number }
  | { kind: 'weekdays'; hour: number; minute: number }
  | { kind: 'everyHours'; hours: number }

export function cadenceCron(c: Cadence): string {
  switch (c.kind) {
    case 'daily': return `${c.minute} ${c.hour} * * *`
    case 'weekly': return `${c.minute} ${c.hour} * * ${c.weekday}`
    case 'weekdays': return `${c.minute} ${c.hour} * * 1-5`
    case 'everyHours': return ''
  }
}

function timeLabel(hour: number, minute: number, locale?: string): string {
  return new Date(2024, 0, 2, hour, minute).toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' })
}

function weekdayLabel(weekday: number, locale?: string): string {
  return new Date(Date.UTC(2024, 0, 7 + weekday)).toLocaleDateString(locale, { weekday: 'long', timeZone: 'UTC' })
}

export function cadenceLabel(c: Cadence, locale?: string): string {
  switch (c.kind) {
    case 'daily': return `Every day · ${timeLabel(c.hour, c.minute, locale)}`
    case 'weekly': return `Every ${weekdayLabel(c.weekday, locale)} · ${timeLabel(c.hour, c.minute, locale)}`
    case 'weekdays': return `Every weekday · ${timeLabel(c.hour, c.minute, locale)}`
    case 'everyHours': return c.hours === 1 ? 'Every hour' : `Every ${c.hours} hours`
  }
}

export interface TriggerPrefill {
  id: string
  name: string
  cadence: Cadence
  provider: string
  config: Record<string, unknown>
}

export function prefillDraft(p: TriggerPrefill): ScheduleDraft {
  const base = emptyDraft()
  if (p.cadence.kind === 'everyHours')
    return { ...base, kind: 'every', intervalValue: p.cadence.hours, intervalUnit: 'h' }
  return { ...base, kind: 'cron', cron: cadenceCron(p.cadence) }
}

function preset(p: {
  id: string
  icon: LucideIcon
  title: string
  description: string
  cadence: Cadence
  provider: string
  config: Record<string, unknown>
}): PresetDef<TriggerPrefill> {
  return {
    id: p.id,
    icon: p.icon,
    title: p.title,
    summary: cadenceLabel(p.cadence),
    description: p.description,
    prefill: { id: p.id, name: p.title, cadence: p.cadence, provider: p.provider, config: p.config },
  }
}

export const TRIGGER_PRESETS: PresetDef<TriggerPrefill>[] = [
  preset({
    id: 'morning-briefing',
    icon: Sunrise,
    title: 'Morning briefing',
    description: 'An agent writes you a short start-of-day briefing.',
    cadence: { kind: 'daily', hour: 8, minute: 0 },
    provider: 'invoke-agent',
    config: {
      task_template:
        'Write a short morning briefing: what is waiting in my inbox, what is scheduled today, '
        + 'and the three things most worth my attention. Keep it under 200 words.',
    },
  }),
  preset({
    id: 'weekly-digest',
    icon: Newspaper,
    title: 'Weekly digest',
    description: 'A once-a-week summary of what moved and what stalled.',
    cadence: { kind: 'weekly', weekday: 1, hour: 9, minute: 0 },
    provider: 'invoke-agent',
    config: {
      task_template:
        'Summarize the past week: what got finished, what is still open, and what slipped. '
        + 'Group it by project and end with what to pick up first this week.',
    },
  }),
  preset({
    id: 'nightly-check',
    icon: Moon,
    title: 'Nightly check',
    description: 'An agent looks for anything left broken before you stop for the day.',
    cadence: { kind: 'daily', hour: 23, minute: 0 },
    provider: 'invoke-agent',
    config: {
      task_template:
        'Check the projects I touched today for anything left in a broken state — failing tests, '
        + 'uncommitted work, an unfinished edit — and list what needs picking up tomorrow.',
    },
  }),
  preset({
    id: 'standup-reminder',
    icon: BellRing,
    title: 'Standup reminder',
    description: 'A plain desktop notification on weekdays — no agent run.',
    cadence: { kind: 'weekdays', hour: 9, minute: 45 },
    provider: 'notify',
    config: {
      title_template: 'Standup in 15 minutes',
      body_template: 'Jot down what you finished yesterday and what you are picking up today.',
    },
  }),
]

export function findTriggerPreset(id: string): PresetDef<TriggerPrefill> | null {
  if (!id) return null
  return TRIGGER_PRESETS.find((p) => p.id === id) ?? null
}
