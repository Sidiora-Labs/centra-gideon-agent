import { generativeActions, type LiveUISpec } from './uispec'

export type UISpecAction = { type: string; $input?: Record<string, unknown> }
export type ActionResult = { ok: boolean; outcome: 'opened' | 'confirmed' | 'cancelled' | 'failed' | 'unavailable'; message: string }
type Scope = { producer: string; recordId: string }
export type ActionCapability = Scope & (
  | { kind: 'open-record'; open: (scope: Scope) => boolean | Promise<boolean> }
  | { kind: 'open-authorized-flow'; open: (scope: Scope & { fields: Record<string, unknown> }) => boolean | Promise<boolean> }
  | { kind: 'preview-confirm'; previewAndConfirm: (scope: Scope & { fields: Record<string, unknown> }) =>
      Promise<{ status: 'confirmed' | 'cancelled' | 'failed'; message: string }> }
)
export type ActionCapabilities = Partial<Record<string, ActionCapability>>

const mutations = new Set([
  'book_stay', 'book_reservation', 'contact_support', 'add_to_calendar',
  'send_email', 'add_to_cart', 'purchase_cart', 'confirm_delete',
  'create_task', 'confirm_purchase',
])

function validFields(value: unknown): value is Record<string, unknown> {
  if (value === undefined) return true
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length > 32) return false
  const seen = new Set<object>()
  function visit(item: unknown, depth: number): boolean {
    if (item === null || typeof item === 'boolean') return true
    if (typeof item === 'string') return item.length <= 4096
    if (typeof item === 'number') return Number.isFinite(item)
    if (depth > 4 || typeof item !== 'object' || item === null || seen.has(item)) return false
    seen.add(item)
    if (Array.isArray(item)) return item.length <= 32 && item.every((part) => visit(part, depth + 1))
    return Object.keys(item).length <= 32 && Object.entries(item).every(([key, part]) =>
      key !== '__proto__' && key !== 'constructor' && key !== 'prototype' && visit(part, depth + 1))
  }
  try { return visit(value, 0) && JSON.stringify(value).length <= 8192 } catch { return false }
}

export function availableCapability(
  spec: LiveUISpec, action: string, capabilities: ActionCapabilities,
): ActionCapability | null {
  if (!(generativeActions[spec.template] as readonly string[]).includes(action)) return null
  const capability = capabilities[action]
  if (!capability || capability.producer !== spec.producer || capability.recordId !== spec.recordId) return null
  if (mutations.has(action) && capability.kind !== 'preview-confirm' && capability.kind !== 'open-authorized-flow') return null
  return capability
}

export async function dispatchUISpecAction(
  spec: LiveUISpec, action: unknown, capabilities: ActionCapabilities,
): Promise<ActionResult> {
  if (!action || typeof action !== 'object' || Array.isArray(action)) {
    return { ok: false, outcome: 'unavailable', message: 'Invalid action payload.' }
  }
  const data = action as Record<string, unknown>
  if (typeof data.type !== 'string' || !validFields(data.$input) ||
      Object.keys(data).some((key) => key !== 'type' && key !== '$input')) {
    return { ok: false, outcome: 'unavailable', message: 'Invalid action payload.' }
  }
  const capability = availableCapability(spec, data.type, capabilities)
  if (!capability) return { ok: false, outcome: 'unavailable', message: 'This action has no connected provider or authorized route.' }
  const scope = { producer: spec.producer, recordId: spec.recordId }
  try {
    if (capability.kind === 'open-record') {
      const opened = await capability.open(scope)
      return opened
        ? { ok: true, outcome: 'opened', message: 'Record opened.' }
        : { ok: false, outcome: 'unavailable', message: 'The record route is unavailable.' }
    }
    if (capability.kind === 'open-authorized-flow') {
      const opened = await capability.open({ ...scope, fields: (data.$input as Record<string, unknown> | undefined) ?? {} })
      return opened
        ? { ok: true, outcome: 'opened', message: 'Review flow opened; no change has been made.' }
        : { ok: false, outcome: 'unavailable', message: 'The review flow is unavailable.' }
    }
    const result = await capability.previewAndConfirm({ ...scope, fields: (data.$input as Record<string, unknown> | undefined) ?? {} })
    if (!result || !['confirmed', 'cancelled', 'failed'].includes(result.status) || typeof result.message !== 'string') {
      return { ok: false, outcome: 'failed', message: 'The provider returned an invalid result.' }
    }
    return { ok: result.status === 'confirmed', outcome: result.status, message: result.message }
  } catch (error) {
    return { ok: false, outcome: 'failed', message: error instanceof Error ? error.message : 'The action failed.' }
  }
}
