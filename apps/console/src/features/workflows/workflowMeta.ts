import { CircleCheck, CircleDashed, CircleSlash, Clock, Loader2, OctagonAlert, Pause, TriangleAlert, type LucideIcon } from 'lucide-react'
import type { WorkflowRunStatus } from '../../shared/data/api'

export interface StatusLook { label: string; icon: LucideIcon; tone: string; spin?: boolean }

const RUN_LOOK: Record<WorkflowRunStatus, StatusLook> = {
  draft: { label: 'Draft', icon: CircleDashed, tone: 'text-on-surface-low' },
  running: { label: 'Running', icon: Loader2, tone: 'text-on-surface', spin: true },
  paused: { label: 'Paused', icon: Pause, tone: 'text-on-surface-low' },
  needs_input: { label: 'Needs you', icon: TriangleAlert, tone: 'text-warning' },
  complete: { label: 'Completed', icon: CircleCheck, tone: 'text-success' },
  failed: { label: 'Failed', icon: OctagonAlert, tone: 'text-danger' },
  cancelled: { label: 'Cancelled', icon: CircleSlash, tone: 'text-on-surface-low' },
  escalated: { label: 'Escalated', icon: TriangleAlert, tone: 'text-danger' },
}

export function runLook(status: string): StatusLook {
  return RUN_LOOK[status as WorkflowRunStatus] ?? { label: status || 'Unknown', icon: CircleDashed, tone: 'text-on-surface-low' }
}

const NODE_LOOK: Record<string, StatusLook> = {
  pending: { label: 'Pending', icon: CircleDashed, tone: 'text-on-surface-low' },
  ready: { label: 'Ready', icon: CircleDashed, tone: 'text-on-surface-low' },
  running: { label: 'Running', icon: Loader2, tone: 'text-on-surface', spin: true },
  waiting: { label: 'Waiting', icon: Clock, tone: 'text-warning' },
  done: { label: 'Done', icon: CircleCheck, tone: 'text-success' },
  degraded: { label: 'Degraded', icon: TriangleAlert, tone: 'text-warning' },
  no_change: { label: 'No change', icon: CircleCheck, tone: 'text-on-surface-low' },
  skipped: { label: 'Skipped', icon: CircleSlash, tone: 'text-on-surface-low' },
  failed: { label: 'Failed', icon: OctagonAlert, tone: 'text-danger' },
  scope_violation: { label: 'Scope violation', icon: OctagonAlert, tone: 'text-danger' },
  blocked: { label: 'Blocked', icon: OctagonAlert, tone: 'text-danger' },
  escalated: { label: 'Escalated', icon: TriangleAlert, tone: 'text-danger' },
  cancelled: { label: 'Cancelled', icon: CircleSlash, tone: 'text-on-surface-low' },
  discarded: { label: 'Discarded', icon: CircleSlash, tone: 'text-on-surface-low' },
}

export function nodeLook(state: string): StatusLook {
  return NODE_LOOK[state] ?? { label: state || 'Unknown', icon: CircleDashed, tone: 'text-on-surface-low' }
}

export const TERMINAL_RUN_STATUSES = new Set<string>(['complete', 'failed', 'cancelled', 'escalated'])

export const isTerminal = (status: string) => TERMINAL_RUN_STATUSES.has(status)

export const PRELAUNCH_RUN_STATUSES = new Set<string>(['draft'])

export const isPrelaunch = (status: string) => PRELAUNCH_RUN_STATUSES.has(status)

export const TERMINAL_NODE_STATES = new Set<string>([
  'done', 'degraded', 'failed', 'skipped', 'no_change', 'scope_violation', 'discarded', 'escalated', 'blocked', 'cancelled',
])

export const isNodeTerminal = (state: string) => TERMINAL_NODE_STATES.has(state)

export function nodeLabel(node: { node_id: string; instance_path: string }): string {
  if (node.node_id) {
    const suffix = node.instance_path.match(/[#@]\d+$/)
    return suffix ? `${node.node_id} ${suffix[0]}` : node.node_id
  }
  return node.instance_path
}

export function itemProgress(node: {
  item_index?: number; item_total?: number; item_label?: string
}): string {
  const parts: string[] = []
  if (typeof node.item_index === 'number') {
    parts.push(node.item_total
      ? `[${node.item_index + 1}/${node.item_total}]`
      : `[${node.item_index + 1}]`)
  }
  if (node.item_label) parts.push(node.item_label)
  return parts.join(' ')
}

export function nodeDepth(instancePath: string): number {
  return Math.max(0, (instancePath.match(/\.(children\[\d+\]|body|cases\[[^\]]*\]|default)/g) ?? []).length - 1)
}

export function fmtElapsed(secs: number | undefined): string {
  if (!secs || secs <= 0) return ''
  if (secs < 60) return `${Math.round(secs)}s`
  const m = Math.floor(secs / 60)
  if (m < 60) return `${m}m ${Math.round(secs % 60)}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}
