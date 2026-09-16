
import { activePhaseIndex, phaseForCycle, type Phase } from './loopPhases'

export type RunStepState = 'done' | 'active' | 'todo'
export interface RunStep { label: string; state: RunStepState; key: string }

export interface GateFailure { label: string; command: string; output: string }
export interface StallInfo { stage: string; title: string; findings: number }

export interface RunFlags {
  gate: GateFailure | null
  stall: StallInfo | null
  judgeDegraded: boolean
  deleted: boolean
}

export const emptyRunFlags = (): RunFlags => ({ gate: null, stall: null, judgeDegraded: false, deleted: false })

export interface RunViewModel {
  id: string
  kind: string
  phased: boolean
  status: string
  parked: boolean
  totalCycles: number
  maxCycles: number
  phaseDone: number
  phaseTotal: number
  progressLabel: string
  steps: RunStep[]
  activePhase: number
  bestScore: number | null
  lastScore: number | null
  marginals: number[]
  elapsedSeconds: number
  gate: GateFailure | null
  stall: StallInfo | null
  judgeDegraded: boolean
}

const PARKED = new Set(['blocked', 'needs_input', 'stagnant', 'failed', 'stopped', 'ended_early'])

const GOAL_TYPE_LABEL: Record<string, string> = {
  open_ended: 'Open-ended', verifiable: 'Verifiable', monitor: 'Monitoring',
}

export interface RunSnapshot {
  id: string
  kind: string
  status: string
  total_cycles?: number
  max_cycles?: number
  plan?: Phase[]
  phase_status?: Record<string, string>
  kind_config?: Record<string, unknown>
  elapsed_seconds?: number
  best_score?: number
  last_score?: number
  marginal_scores?: number[]
}

export type RunSnapshotViewModel = Omit<RunViewModel, keyof RunFlags>

export function foldRunSnapshot(loop: RunSnapshot): RunSnapshotViewModel {
  const kind = loop.kind
  const phased = kind !== 'goal'
  const kc = (loop.kind_config || {}) as Record<string, unknown>
  const totalCycles = loop.total_cycles ?? 0
  const plan = loop.plan || []

  let phaseDone = 0, phaseTotal = 0, progressLabel = ''
  const steps: RunStep[] = []
  if (phased) {
    const ss = loop.phase_status || {}
    phaseTotal = plan.length
    phaseDone = Object.values(ss).filter((s) => s === 'done').length
    progressLabel = plan.length ? `${phaseDone}/${phaseTotal} stages` : ''
    for (const s of plan) {
      const skey = (String((s as Record<string, unknown>).stage ?? '').trim()
        || String((s as Record<string, unknown>).title ?? '').trim())
      const st = ss[skey]
      steps.push({
        key: skey,
        label: String((s as Record<string, unknown>).title || (s as Record<string, unknown>).stage || ''),
        state: st === 'done' ? 'done' : (st === 'active' || st === 'running') ? 'active' : 'todo',
      })
    }
  } else {
    progressLabel = GOAL_TYPE_LABEL[String(kc.goal_type ?? '')] ?? ''
    const subs = Array.isArray(kc.sub_goals) ? (kc.sub_goals as string[]) : []
    for (const g of subs) steps.push({ key: g, label: g, state: 'todo' })
  }

  const scoreSrc = { ...kc, ...(loop as unknown as Record<string, unknown>) }
  const bestScore = typeof scoreSrc.best_score === 'number' ? scoreSrc.best_score : null
  const lastScore = typeof scoreSrc.last_score === 'number' ? scoreSrc.last_score : null
  const marginals = Array.isArray(loop.marginal_scores) ? loop.marginal_scores.slice(-16) : []

  const activePhase = phased ? activePhaseIndex(totalCycles, plan) : phaseForCycle(totalCycles, plan)

  return {
    id: loop.id,
    kind,
    phased,
    status: loop.status,
    parked: PARKED.has(loop.status),
    totalCycles,
    maxCycles: loop.max_cycles ?? 0,
    phaseDone,
    phaseTotal,
    progressLabel,
    steps,
    activePhase,
    bestScore,
    lastScore,
    marginals,
    elapsedSeconds: loop.elapsed_seconds ?? 0,
  }
}

export function foldReducer(flags: RunFlags, event: string, data?: unknown): RunFlags {
  const d = (data ?? {}) as Record<string, unknown>
  switch (event) {
    case 'deleted':
      return { ...flags, deleted: true }
    case 'gate_check': {
      if (d.ok === false) {
        return { ...flags, gate: { label: String(d.label || 'check'), command: String(d.command || ''), output: String(d.output || '') } }
      }
      return { ...flags, gate: null }
    }
    case 'stage_stalled':
      return { ...flags, stall: { stage: String(d.stage || ''), title: String(d.title || d.stage || 'this stage'), findings: Number(d.findings || 0) } }
    case 'judge_error':
      return { ...flags, judgeDegraded: true }
    case 'cycle_verdict':
      return { ...flags, judgeDegraded: false, stall: null, gate: null }
    case 'blocked':
      return { ...flags, gate: null }
    default:
      return { ...flags, stall: null, gate: null }
  }
}

export function foldRun(loop: RunSnapshot, flags: RunFlags = emptyRunFlags()): RunViewModel {
  return {
    ...foldRunSnapshot(loop),
    gate: flags.gate,
    stall: flags.stall,
    judgeDegraded: flags.judgeDegraded,
  }
}
