import type { OwnerScope } from '../../shared/auth.web'
import type { ShellReturnContext, ShellRoute } from '../../shared/shell/shellRoutes'

export type StudioJobState = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'unknown'

export type StudioRecordRef = Readonly<{
  kind: string
  id: string
  ownerScopeKey: string
  revision?: number
  source?: Readonly<{ conversationId?: string; runId?: string }>
  providerCapability?: string
  job?: Readonly<{ id: string; state: StudioJobState }>
  artifact?: Readonly<{ id: string; version?: number; available: boolean }>
}>

export type StudioModuleProps = Readonly<{
  route: ShellRoute
  scope: OwnerScope
  navigate: (route: ShellRoute) => void
  returnTo?: ShellReturnContext
  onReturn: () => void
}>

const validText = (value: string): boolean => value.trim().length > 0 && value.length <= 512
  && !/[\u0000-\u001f\u007f]/.test(value)

export function studioRecordRef(scope: OwnerScope, native: Omit<StudioRecordRef, 'ownerScopeKey'>): StudioRecordRef {
  if (!scope.cacheKey || !validText(native.kind) || !validText(native.id)
    || (native.revision !== undefined && (!Number.isSafeInteger(native.revision) || native.revision < 1))
    || (native.artifact?.version !== undefined && (!Number.isSafeInteger(native.artifact.version) || native.artifact.version < 1))
    || [native.source?.conversationId, native.source?.runId, native.providerCapability,
      native.job?.id, native.artifact?.id].some(value => value !== undefined && !validText(value))) {
    throw new TypeError('A native Studio record, revision, and owner are required')
  }
  return Object.freeze({ ...native, ownerScopeKey: scope.cacheKey })
}

export function sameStudioOwner(scope: OwnerScope, ref: StudioRecordRef): boolean {
  return !!scope.cacheKey && ref.ownerScopeKey === scope.cacheKey
}
