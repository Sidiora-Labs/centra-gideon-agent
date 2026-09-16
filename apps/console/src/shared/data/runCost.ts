import type { LoopSpend } from './api'

export function runUsd(costUsd: number): string {
  return costUsd >= 1 ? `$${costUsd.toFixed(2)}` : `$${costUsd.toFixed(4)}`
}

export function runCostText(costUsd: number, priced: boolean): string {
  if (!priced) {
    return costUsd > 0
      ? `At least ~${runUsd(costUsd)} this run — some step recorded no cost, so the real total is higher`
      : 'Not recorded — no step on this run booked a cost, so nothing here says what it spent'
  }
  if (!(costUsd > 0)) return 'Nothing — every step was measured and cost nothing (a free local model)'
  return `~${runUsd(costUsd)} this run — estimated from model prices, not a provider-reported charge`
}

export function runCostStat(costUsd: number, priced: boolean): string {
  if (!priced) return costUsd > 0 ? `≥~${runUsd(costUsd)}` : 'not recorded'
  return `~${runUsd(costUsd)}`
}

export function templateCostStat(costUsd: number, priced: boolean): string {
  return priced ? runUsd(costUsd) : `≥${runUsd(costUsd)}`
}

export function loopSpendPill(spend: LoopSpend): string {
  if (!(spend.dollars_est > 0)) {
    return spend.planning.dollars_est > 0
      ? `~${runUsd(spend.planning.dollars_est)} planning`
      : 'no spend recorded'
  }
  const base = `~${runUsd(spend.dollars_est)}`
  return spend.planning.dollars_est > 0
    ? `${base} + ~${runUsd(spend.planning.dollars_est)} planning`
    : base
}

export function loopSpendTitle(spend: LoopSpend): string {
  if (!(spend.dollars_est > 0) && !(spend.planning.dollars_est > 0)) {
    return 'No model spend recorded for this loop — a local model, or one with no price row.'
  }
  const parts: string[] = []
  parts.push(
    spend.dollars_est > 0
      ? `~${runUsd(spend.dollars_est)} across ${spend.turns} ${spend.turns === 1 ? 'turn' : 'turns'} of this loop's worker and its task workers`
      : `No worker spend recorded yet`,
  )
  if (spend.planning.dollars_est > 0) {
    parts.push(
      `~${runUsd(spend.planning.dollars_est)} more in planning, counted separately because the planner runs as its own session`,
    )
  }
  parts.push(
    spend.priced
      ? 'Estimated from model prices, not a provider-reported charge'
      : 'At least this much — some model had no price row, so the real total is higher',
  )
  return parts.join('. ') + '.'
}
