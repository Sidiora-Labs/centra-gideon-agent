import type { HypermidAuthorityPlanWire } from '../../shared/data/api'

export function authorityPlanDecision(plan: HypermidAuthorityPlanWire, reviewedDigest: string, now = Date.now()): { allowed: boolean; reason: string } {
  if (reviewedDigest !== plan.plan_digest) return { allowed: false, reason: 'Review this exact authority plan before applying it.' }
  if (!Number.isFinite(Date.parse(plan.expires_at)) || Date.parse(plan.expires_at) <= now) return { allowed: false, reason: 'This authority plan expired. Prepare a new plan.' }
  if (plan.blockers.length) return { allowed: false, reason: 'Resolve every authority blocker and prepare a new plan.' }
  return { allowed: true, reason: '' }
}
