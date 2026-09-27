import type { OwnerScope } from '../../shared/auth.web'
import { GatewayError, gatewayHeaders, gatewayJson, gatewayPath, gatewayRequestInit, readGatewayJson } from '../../shared/transport.web'
import type { BrowserFailureKind, BrowserMutation, BrowserPreview, BrowserResult, BrowserSession } from './browserTypes'

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('Invalid browser session response')
  return value as Record<string, unknown>
}

function session(value: unknown): BrowserSession {
  const row = object(object(value).session)
  if (typeof row.id !== 'string' || !row.id || typeof row.conversation_id !== 'string' || !row.conversation_id ||
      !['reserved', 'active', 'closed', 'error'].includes(String(row.status)) ||
      !Number.isSafeInteger(row.version) || Number(row.version) < 1 ||
      typeof row.created_at !== 'number' || typeof row.updated_at !== 'number' ||
      !['assistant', 'customer'].includes(String(row.control_holder))) {
    throw new TypeError('Invalid browser session response')
  }
  return Object.freeze({
    id: row.id, conversationId: row.conversation_id, status: row.status as BrowserSession['status'],
    version: row.version as number, createdAt: row.created_at, updatedAt: row.updated_at,
    controlHolder: row.control_holder as BrowserSession['controlHolder'],
  })
}

function failure(error: unknown): { state: BrowserFailureKind; message: string } {
  if (error instanceof GatewayError) {
    if (error.status === 401 || error.authRequired) return { state: 'signed-out', message: 'Sign in to open this browser.' }
    if (error.status === 403) return { state: 'denied', message: 'This browser is not available to your account.' }
    if (error.status === 404) return { state: 'missing', message: 'This conversation or browser is unavailable.' }
    if (error.status === 410) return { state: 'expired', message: 'This browser session has expired.' }
    if (error.status === 409) return { state: 'stale-version', message: 'The browser changed. Refresh its state before acting.' }
    if (error.status === 501 || error.status === 503) return { state: 'unavailable', message: 'The browser service is unavailable. Try again later.' }
  }
  if (error instanceof TypeError && error.message.startsWith('Invalid browser'))
    return { state: 'invalid-response', message: 'The browser returned an invalid state. Refresh the workspace.' }
  return { state: 'retryable', message: 'The browser connection was interrupted. Refresh its state.' }
}

function path(id: string): string {
  if (!id || id.length > 255) throw new TypeError('Invalid browser session ID')
  return `/api/browser/sessions/${encodeURIComponent(id)}`
}

export class BrowserClient {
  private readonly opening = new Map<string, Promise<BrowserResult<BrowserSession>>>()
  private readonly writes = new Map<string, Promise<BrowserResult<BrowserSession>>>()

  constructor(readonly scope: OwnerScope) {
    if (!scope.ownerId || !scope.cacheKey || !scope.runtimeOrigin ||
        (typeof window !== 'undefined' && scope.runtimeOrigin !== window.location.origin))
      throw new TypeError('An authenticated same-origin owner is required')
  }

  async get(id: string): Promise<BrowserResult<BrowserSession>> {
    try { return { state: 'ready', value: session(await gatewayJson(path(id))) } }
    catch (error) { return failure(error) }
  }

  open(conversationId: string): Promise<BrowserResult<BrowserSession>> {
    if (!conversationId || conversationId.length > 255) return Promise.resolve({ state: 'missing', message: 'Open a saved conversation first.' })
    const pending = this.opening.get(conversationId)
    if (pending) return pending
    const operation = this.reserve(conversationId).finally(() => this.opening.delete(conversationId))
    this.opening.set(conversationId, operation)
    return operation
  }

  private async reserve(conversationId: string): Promise<BrowserResult<BrowserSession>> {
    const create = () => gatewayJson('/api/browser/sessions', { method: 'POST', body: { conversation_id: conversationId } })
    try {
      let reply: unknown
      try { reply = await create() }
      catch (error) {
        if (failure(error).state !== 'retryable') throw error
        reply = await create()
      }
      const row = session(reply)
      return { state: 'ready', value: row }
    } catch (error) { return failure(error) }
  }

  mutate(current: BrowserSession, action: BrowserMutation, body: Record<string, unknown> = {}): Promise<BrowserResult<BrowserSession>> {
    return this.write(current, action, body)
  }

  navigate(current: BrowserSession, url: string): Promise<BrowserResult<BrowserSession>> {
    return this.write(current, 'navigate', { url })
  }

  input(current: BrowserSession, command: string, value: string): Promise<BrowserResult<BrowserSession>> {
    return this.write(current, 'input', { command, value })
  }

  private write(current: BrowserSession, action: BrowserMutation | 'navigate' | 'input', body: Record<string, unknown>): Promise<BrowserResult<BrowserSession>> {
    const key = `${current.id}:${action}`
    const pending = this.writes.get(key)
    if (pending) return pending
    const operation = this.perform(current, action, body).finally(() => this.writes.delete(key))
    this.writes.set(key, operation)
    return operation
  }

  private async perform(current: BrowserSession, action: BrowserMutation | 'navigate' | 'input', body: Record<string, unknown>): Promise<BrowserResult<BrowserSession>> {
    const endpoint = action === 'takeover' || action === 'handback' ? `/control/${action}` : `/${action}`
    try {
      const result = session(await gatewayJson(`${path(current.id)}${endpoint}`, {
        method: 'POST', body: { ...body, expected_version: current.version },
      }))
      if (result.id !== current.id || result.conversationId !== current.conversationId)
        throw new TypeError('Invalid browser session identity')
      return { state: 'ready', value: result }
    } catch (error) {
      const classified = failure(error)
      if (classified.state !== 'retryable' && classified.state !== 'stale-version') return classified
      const read = await this.get(current.id)
      if (read.state === 'ready') {
        if (read.value.conversationId !== current.conversationId)
          return { state: 'invalid-response', message: 'The browser identity changed. Return to the conversation.' }
        return { state: classified.state, message: classified.message, current: read.value }
      }
      return read
    }
  }

  async preview(current: BrowserSession): Promise<BrowserResult<BrowserPreview>> {
    try {
      const response = await fetch(gatewayPath(`${path(current.id)}/preview`), {
        ...gatewayRequestInit('GET'), headers: gatewayHeaders(false),
      })
      if (!response.ok) await readGatewayJson(response)
      const version = Number(response.headers.get('X-Browser-Version'))
      const timestamp = Number(response.headers.get('X-Browser-Timestamp'))
      const holder = response.headers.get('X-Browser-Control')
      if (!Number.isSafeInteger(version) || !Number.isFinite(timestamp) ||
          (holder !== 'assistant' && holder !== 'customer')) throw new TypeError('Invalid browser preview response')
      return { state: 'ready', value: {
        image: await response.blob(), version, timestamp, controlHolder: holder,
        url: decodeURIComponent(response.headers.get('X-Browser-Url') ?? ''),
        title: decodeURIComponent(response.headers.get('X-Browser-Title') ?? ''),
      } }
    } catch (error) { return failure(error) }
  }
}
