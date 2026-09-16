
export type Phase = Record<string, unknown>

export function activePhaseIndex(totalCycles: number, plan: Phase[]): number {
  if (!plan.length) return -1
  let elapsed = 0
  for (let i = 0; i < plan.length; i++) {
    elapsed += Math.max(1, Number(plan[i].min_cycles) || 1)
    if (totalCycles < elapsed) return i
  }
  return plan.length - 1
}

export function phaseMinCycles(p: Phase): number {
  return Math.max(1, Number(p.min_cycles) || 1)
}

export function hasDistinctName(name: string, goal: string): boolean {
  const n = (name ?? '').replace(/[…\s]+$/u, '').trim().toLowerCase()
  const g = (goal ?? '').trim().toLowerCase()
  if (!n) return false
  return !g.startsWith(n)
}

export function phaseForCycle(cycle: number, plan: Phase[]): number {
  if (!plan.length) return -1
  let end = 0
  for (let i = 0; i < plan.length; i++) {
    end += phaseMinCycles(plan[i])
    if (cycle <= end) return i
  }
  return plan.length - 1
}
