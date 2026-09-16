
import type { WorkflowNodeState, WorkflowRunDetailData } from '../../shared/data/api'
import { byInstancePath } from './instancePathOrder'
import type { WorkflowLifecycleEvent } from './useWorkflowStream'

export interface WorkflowEventEnvelope {
  run_id?: string
  event_id?: string
  seq?: number
  epoch?: number
  node_epoch?: number
  node_id?: string
  instance_path?: string
  status?: string
  degraded_reason?: string
  cached?: boolean
  item_index?: number
  item_total?: number
  item_label?: string
  [key: string]: unknown
}

export interface WorkflowViewModel {
  runId: string
  workflow: string
  status: string
  specVersion: number
  error: string
  nodes: WorkflowNodeState[]
  doneCount: number
  totalCount: number
  progress: number
  tokens: number
  elapsedSecs: number
  live: boolean
  needsInput: boolean
  attention: Record<string, unknown> | null
  epoch: number
  seen: ReadonlySet<string>
  nodeSeq: ReadonlyMap<string, number>
  dropped: number
}

const TERMINAL_NODE = new Set([
  'done', 'degraded', 'failed', 'skipped', 'no_change', 'scope_violation',
  'discarded', 'escalated', 'blocked', 'cancelled',
])
const TERMINAL_RUN = new Set(['complete', 'failed', 'cancelled', 'escalated'])

export function foldSnapshot(snap: WorkflowRunDetailData): WorkflowViewModel {
  const nodes = [...(snap.nodes ?? [])].sort(byInstancePath)
  const done = nodes.filter((n) => TERMINAL_NODE.has(n.state)).length
  return {
    runId: snap.run_id,
    workflow: snap.workflow,
    status: snap.status,
    specVersion: snap.spec_version,
    error: snap.error ?? '',
    nodes,
    doneCount: done,
    totalCount: nodes.length,
    progress: nodes.length ? done / nodes.length : 0,
    tokens: snap.tokens ?? 0,
    elapsedSecs: snap.elapsed_secs ?? 0,
    live: !TERMINAL_RUN.has(snap.status),
    needsInput: snap.status === 'needs_input',
    attention: snap.attention ?? null,
    epoch: 0,
    seen: new Set<string>(),
    nodeSeq: new Map<string, number>(),
    dropped: 0,
  }
}

export function foldEvent(
  vm: WorkflowViewModel,
  event: WorkflowLifecycleEvent,
  data: unknown,
): WorkflowViewModel {
  const env = (data ?? {}) as WorkflowEventEnvelope

  if (env.run_id && vm.runId && env.run_id !== vm.runId) {
    return { ...vm, dropped: vm.dropped + 1 }
  }

  if (env.event_id && vm.seen.has(env.event_id)) {
    return { ...vm, dropped: vm.dropped + 1 }
  }

  const epoch = typeof env.epoch === 'number' ? env.epoch : vm.epoch
  if (epoch < vm.epoch) {
    return { ...vm, dropped: vm.dropped + 1 }
  }

  const seen = env.event_id ? new Set(vm.seen).add(env.event_id) : vm.seen
  let next: WorkflowViewModel = { ...vm, seen, epoch: Math.max(vm.epoch, epoch) }

  switch (event) {
    case 'workflow_run_update':
      if (typeof env.status === 'string') next = applyRunStatus(next, env.status)
      if (typeof env.error === 'string') next.error = env.error
      break

    case 'workflow_node_started':
      next = patchNode(next, env, 'running')
      break

    case 'workflow_node_done':
      next = patchNode(next, env, typeof env.status === 'string' ? env.status : 'done')
      break

    case 'workflow_attention':
    case 'workflow_needs_input':
      next.attention = (env.ask as Record<string, unknown>) ?? next.attention
      next = applyRunStatus(next, 'needs_input')
      break

    case 'workflow_gate_resolved':
      next.attention = null
      if (env.instance_path) next = patchNode(next, env, 'done')
      break

    case 'workflow_gate_revised':
      next.attention = null
      if (env.instance_path) next = patchNode(next, env, 'running')
      break

    case 'workflow_spec_updated':
      if (typeof env.spec_version === 'number') next.specVersion = env.spec_version
      break

    case 'workflow_progress':
      if (Array.isArray(env.nodes)) next = applyProgress(next, env.nodes as WorkflowNodeState[])
      if (typeof env.tokens === 'number') next.tokens = env.tokens
      break

    case 'workflow_forked':
    case 'workflow_mutation_rejected':
      break
  }

  return recount(next)
}

export function foldEvents(
  vm: WorkflowViewModel,
  events: Array<{ event: WorkflowLifecycleEvent; data: unknown }>,
): WorkflowViewModel {
  return events.reduce((acc, e) => foldEvent(acc, e.event, e.data), vm)
}

function applyRunStatus(vm: WorkflowViewModel, status: string): WorkflowViewModel {
  return {
    ...vm,
    status,
    live: !TERMINAL_RUN.has(status),
    needsInput: status === 'needs_input',
    attention: TERMINAL_RUN.has(status) ? null : vm.attention,
  }
}

function patchNode(
  vm: WorkflowViewModel,
  env: WorkflowEventEnvelope,
  state: string,
): WorkflowViewModel {
  const path = env.instance_path
  if (!path) return vm

  const seq = typeof env.seq === 'number' ? env.seq : null
  const applied = vm.nodeSeq.get(path)
  if (seq !== null && applied !== undefined && seq < applied) {
    return { ...vm, dropped: vm.dropped + 1 }
  }
  const nodeSeq = seq === null ? vm.nodeSeq : new Map(vm.nodeSeq).set(path, seq)

  const existing = vm.nodes.find((n) => n.instance_path === path)
  const patched: WorkflowNodeState = {
    instance_path: path,
    node_id: (env.node_id as string) || existing?.node_id || '',
    state,
    attempt: existing?.attempt,
    degraded_reason: (env.degraded_reason as string) || '',
    failure: existing?.failure ?? null,
    item_index: env.item_index ?? existing?.item_index,
    item_total: env.item_total ?? existing?.item_total,
    item_label: env.item_label ?? existing?.item_label,
  }

  const nodes = existing
    ? vm.nodes.map((n) => (n.instance_path === path ? patched : n))
    : [...vm.nodes, patched].sort(byInstancePath)

  return { ...vm, nodes, nodeSeq }
}

function applyProgress(vm: WorkflowViewModel, incoming: WorkflowNodeState[]): WorkflowViewModel {
  const byPath = new Map(vm.nodes.map((n) => [n.instance_path, n]))
  for (const n of incoming) {
    const prior = byPath.get(n.instance_path)
    byPath.set(n.instance_path, prior ? { ...prior, state: n.state } : { ...n, node_id: n.node_id ?? '' })
  }
  return {
    ...vm,
    nodes: [...byPath.values()].sort(byInstancePath),
  }
}

function recount(vm: WorkflowViewModel): WorkflowViewModel {
  const done = vm.nodes.filter((n) => TERMINAL_NODE.has(n.state)).length
  return {
    ...vm,
    doneCount: done,
    totalCount: vm.nodes.length,
    progress: vm.nodes.length ? done / vm.nodes.length : 0,
  }
}

export function dedupKey(env: WorkflowEventEnvelope): string {
  return [
    env.run_id ?? '',
    env.node_id ?? env.instance_path ?? '',
    env.node_epoch ?? env.epoch ?? 0,
    env.seq ?? 0,
    env.status ?? '',
  ].join('|')
}
