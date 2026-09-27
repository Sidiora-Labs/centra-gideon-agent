import { readOwnerSession, type OwnerScope } from '../../shared/auth.web'
import { GatewayError, gatewayJson } from '../../shared/transport.web'
import type { PendingApproval } from '../../../../console/src/shared/data/api'

export type ApprovalDecision = 'approve' | 'reject'

function stableValue(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableValue).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.entries(value as Record<string, unknown>).sort(([left], [right]) => left.localeCompare(right))
      .map(([key, entry]) => `${JSON.stringify(key)}:${stableValue(entry)}`).join(',')}}`
  }
  return JSON.stringify(value) ?? 'undefined'
}

export function sameApprovalTarget(left: PendingApproval, right: PendingApproval): boolean {
  return left.id === right.id && left.revision === right.revision && left.source === right.source
    && left.tool === right.tool && stableValue(left.tool_input) === stableValue(right.tool_input)
    && left.tool_purpose === right.tool_purpose && left.session === right.session && left.ts === right.ts
}

async function verifyOwner(scope: OwnerScope, signal?: AbortSignal): Promise<void> {
  if (!scope.ownerId || !scope.cacheKey || typeof location === 'undefined' || scope.runtimeOrigin !== location.origin) {
    throw new GatewayError('Sign in to the record owner account to decide this request.', 403)
  }
  const session = await readOwnerSession(signal)
  if (session.user !== scope.ownerId) {
    throw new GatewayError('Sign in to the record owner account to decide this request.', 403)
  }
}

export async function readPendingApproval(scope: OwnerScope, id: string,
  signal?: AbortSignal): Promise<PendingApproval | undefined> {
  await verifyOwner(scope, signal)
  const approvals = await gatewayJson<unknown>('/api/approvals', { signal })
  if (!Array.isArray(approvals)) throw new TypeError('Gideon returned an invalid approval collection')
  const value = approvals.find(item => Boolean(item && typeof item === 'object'
    && (item as Record<string, unknown>).id === id))
  if (value === undefined) return undefined
  if (!value || typeof value !== 'object' || typeof (value as Record<string, unknown>).revision !== 'string'
    || !(value as Record<string, unknown>).revision) {
    throw new TypeError('Gideon returned an approval without a native revision')
  }
  return value as PendingApproval
}

export async function decideApproval(scope: OwnerScope, target: PendingApproval, decision: ApprovalDecision,
  signal?: AbortSignal): Promise<void> {
  if (!target.revision) {
    throw new GatewayError('This approval has no native revision and cannot be decided from Activity.', 501)
  }
  const current = await readPendingApproval(scope, target.id, signal)
  if (!current) throw new GatewayError('This approval is no longer pending.', 404)
  if (!sameApprovalTarget(current, target)) {
    throw new GatewayError('This approval changed. Review the refreshed request before deciding.', 409,
      'revision_conflict')
  }
  await gatewayJson<{ ok: true }>(`/api/approvals/${encodeURIComponent(target.id)}/${decision}`, {
    method: 'POST', body: { expected_revision: target.revision }, signal,
  })
}
