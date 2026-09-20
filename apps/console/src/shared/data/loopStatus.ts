import type { CSSProperties } from 'react'

export interface LoopStatusLook {
  label: string
  accent: string
}

const NEUTRAL_ACCENT = 'var(--color-on-surface-low)'

const LOOP_STATUS: Record<string, LoopStatusLook> = {
  intake: { label: 'Analyzing', accent: 'var(--color-primary)' },
  planning: { label: 'Planning', accent: 'var(--color-primary)' },
  review: { label: 'Review', accent: 'var(--color-info)' },
  ready: { label: 'Ready', accent: 'var(--color-info)' },
  running: { label: 'Running', accent: 'var(--color-primary)' },
  paused: { label: 'Paused', accent: '' },
  stagnant: { label: 'Stalled', accent: 'var(--color-warn)' },
  blocked: { label: 'Blocked', accent: 'var(--color-warn)' },
  needs_input: { label: 'Needs you', accent: 'var(--color-info)' },
  complete: { label: 'Completed', accent: 'var(--color-ok)' },
  failed: { label: 'Failed', accent: 'var(--color-danger)' },
  stopped: { label: 'Stopped', accent: '' },
  ended_early: { label: 'Ended early', accent: 'var(--color-warn)' },
}

export function loopStatusLook(status: string): LoopStatusLook {
  return LOOP_STATUS[status] ?? { label: status, accent: '' }
}

export function loopStatusLabel(status: string): string {
  return loopStatusLook(status).label
}

export function loopStatusColor(status: string): string {
  return loopStatusLook(status).accent || NEUTRAL_ACCENT
}

export function effectiveLoopStatus(status: string, errorMessage?: string | null): string {
  return status === 'complete' && errorMessage ? 'ended_early' : status
}

export function loopStatusTone(status: string, mix = 16): CSSProperties {
  const c = LOOP_STATUS[status]?.accent
  if (!c) return { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }
  return { background: `color-mix(in srgb, ${c} ${mix}%, transparent)`, color: c }
}

export const ACTIVE_LOOP_STATUSES: ReadonlySet<string> = new Set([
  'running', 'paused', 'stagnant', 'blocked', 'needs_input',
])

export function shownCycle(totalCycles: number, status: string): number {
  return totalCycles + (ACTIVE_LOOP_STATUSES.has(status) ? 1 : 0)
}

export const STOPPABLE_LOOP_STATUSES: ReadonlySet<string> = new Set([
  ...ACTIVE_LOOP_STATUSES, 'intake', 'planning',
])

export const PRELAUNCH_LOOP_STATUSES: ReadonlySet<string> = new Set([
  'intake', 'planning', 'review', 'ready',
])

export type LoopAction = 'start' | 'pause' | 'resume' | 'stop'

export const LOOP_ACTION_SOURCE_STATUSES: Readonly<Record<LoopAction, ReadonlySet<string>>> = {
  start: new Set(['ready', 'review']),
  pause: new Set(['running']),
  resume: new Set(['paused', 'stagnant', 'blocked', 'needs_input', 'failed']),
  stop: STOPPABLE_LOOP_STATUSES,
}
