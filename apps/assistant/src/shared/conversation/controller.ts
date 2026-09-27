import { gatewayJson, GatewayError } from '../transport.web'
import type { OwnerScope } from '../auth.web'
import type { ChatDetail, ChatHistoryMessage, ChatSocketEvent, ConversationMessage, ConversationState,
  ConversationStreamCursor } from './types'

function messageId(session: string, message: ChatHistoryMessage, index: number): string {
  const sourceId = message.meta?.id
  if (typeof sourceId === 'string' && sourceId) return `${session}:message:${sourceId}`
  if (message.ts) return `${session}:message:${message.role}:${message.ts}:${index}`
  return `${session}:message:${index}`
}

export function canonicalMessages(detail: ChatDetail): readonly ConversationMessage[] {
  return detail.messages.map((message, index) => ({
    id: messageId(detail.key, message, index),
    role: message.role,
    content: message.content,
    ts: message.ts,
    meta: message.meta,
    streaming: message.role === 'streaming',
  }))
}

export function receivedPrompt(detail: ChatDetail, clientTs: string): boolean {
  return detail.messages.some(message => message.role === 'user' && message.ts === clientTs)
}

export function streamCursorDisposition(
  current: ConversationStreamCursor | null,
  incoming: ConversationStreamCursor,
): 'stale' | 'same-turn' | 'new-turn' {
  if (!current || incoming.stream_epoch !== current.stream_epoch) return 'new-turn'
  if (incoming.stream_turn < current.stream_turn) return 'stale'
  if (incoming.stream_turn > current.stream_turn) return 'new-turn'
  if (incoming.stream_seq <= current.stream_seq) return 'stale'
  return 'same-turn'
}

export function reconcileStreamChunk(
  sessionId: string,
  messages: readonly ConversationMessage[],
  current: ConversationStreamCursor | null,
  content: string,
  incoming: ConversationStreamCursor,
): { messages: readonly ConversationMessage[]; cursor: ConversationStreamCursor } | null {
  const disposition = streamCursorDisposition(current, incoming)
  if (disposition === 'stale') return null
  const next = [...messages]
  let streamingIndex = -1
  if (disposition === 'same-turn') {
    for (let index = next.length - 1; index >= 0; index--) {
      const message = next[index]
      if (message.streaming && message.meta?.stream_epoch === incoming.stream_epoch
        && message.meta?.stream_turn === incoming.stream_turn) {
        streamingIndex = index
        break
      }
    }
  }
  if (disposition === 'new-turn') {
    for (let index = 0; index < next.length; index++) {
      if (next[index].streaming) next[index] = { ...next[index], streaming: false }
    }
  }
  const meta = { stream_epoch: incoming.stream_epoch, stream_turn: incoming.stream_turn,
    stream_seq: incoming.stream_seq }
  if (streamingIndex >= 0) {
    const streaming = next[streamingIndex]
    next[streamingIndex] = { ...streaming, content: streaming.content + content, meta: { ...streaming.meta, ...meta } }
  } else {
    next.push({ id: `${sessionId}:live:${incoming.stream_epoch}:${incoming.stream_turn}`,
      role: 'assistant', content, streaming: true, meta })
  }
  return { messages: next, cursor: incoming }
}

function streamCursor(value: unknown): ConversationStreamCursor | null {
  if (!value || typeof value !== 'object') return null
  const cursor = value as Record<string, unknown>
  if (typeof cursor.stream_epoch !== 'string' || !cursor.stream_epoch
    || typeof cursor.stream_turn !== 'number' || !Number.isInteger(cursor.stream_turn) || cursor.stream_turn < 0
    || typeof cursor.stream_seq !== 'number' || !Number.isInteger(cursor.stream_seq) || cursor.stream_seq < 0) return null
  return { stream_epoch: cursor.stream_epoch, stream_turn: cursor.stream_turn, stream_seq: cursor.stream_seq }
}

const EMPTY: ConversationState = {
  scope: null, sessionId: null, title: '', messages: [], draft: '', phase: 'signed-out',
  connected: false, running: false, error: '',
}

export class ConversationController {
  private state: ConversationState = EMPTY
  private listeners = new Set<() => void>()
  private socket: WebSocket | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private generation = 0
  private loading = false
  private loadToken = 0
  private dirtyDuringLoad = false
  private queuedLiveEvents: ChatSocketEvent[] = []
  private submitting = false
  private submitToken = 0
  private lastChunkSeq = 0
  private streamCursor: ConversationStreamCursor | null = null
  private closed = false

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  snapshot = (): ConversationState => this.state

  private update(patch: Partial<ConversationState>): void {
    this.state = { ...this.state, ...patch }
    for (const listener of this.listeners) listener()
  }

  private clearConnection(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    const socket = this.socket
    this.socket = null
    if (socket) {
      socket.onopen = socket.onclose = socket.onmessage = socket.onerror = null
      socket.close()
    }
  }

  setOwner(scope: OwnerScope | null): void {
    if (scope?.cacheKey === this.state.scope?.cacheKey) return
    this.generation++
    this.loadToken++
    this.clearConnection()
    this.loading = false
    this.dirtyDuringLoad = false
    this.queuedLiveEvents = []
    this.submitting = false
    this.submitToken++
    this.lastChunkSeq = 0
    this.streamCursor = null
    this.update(scope ? { ...EMPTY, scope, phase: 'idle' } : EMPTY)
    if (scope && !this.closed) this.connect()
  }

  setDraft(draft: string): void {
    if (!this.state.scope) return
    this.update({ draft, error: this.state.phase === 'failed' ? '' : this.state.error,
      phase: this.state.phase === 'failed' ? 'ready' : this.state.phase })
  }

  async create(): Promise<string> {
    const scope = this.state.scope
    if (!scope) throw new Error('Sign in to create a conversation')
    const generation = this.generation
    const draft = this.state.draft
    this.update({ phase: 'loading', error: '' })
    const created = await gatewayJson<{ key: string }>('/api/chat/sessions', { method: 'POST', body: {} })
    if (generation !== this.generation || !created.key) throw new Error('Conversation owner changed')
    await this.open(created.key)
    if (scope.cacheKey === this.state.scope?.cacheKey) this.update({ draft })
    return created.key
  }

  async open(sessionId: string): Promise<void> {
    if (!this.state.scope) throw new Error('Sign in to open a conversation')
    if (!sessionId) throw new TypeError('A session ID is required')
    const changed = sessionId !== this.state.sessionId
    this.generation++
    this.loadToken++
    this.clearConnection()
    this.loading = false
    this.dirtyDuringLoad = false
    this.queuedLiveEvents = []
    this.lastChunkSeq = 0
    this.streamCursor = null
    this.update({ sessionId, title: '', messages: [], draft: changed ? '' : this.state.draft,
      phase: 'loading', running: false, error: '' })
    this.connect()
    await this.refresh()
  }

  async refresh(): Promise<void> {
    const { scope, sessionId } = this.state
    if (!scope || !sessionId) return
    if (this.loading) { this.dirtyDuringLoad = true; return }
    const generation = this.generation
    const loadToken = ++this.loadToken
    this.loading = true
    this.dirtyDuringLoad = false
    this.update({ phase: 'recovering', error: '' })
    try {
      const detail = await gatewayJson<ChatDetail>(`/api/chat/sessions/${encodeURIComponent(sessionId)}`)
      if (generation !== this.generation || loadToken !== this.loadToken || scope.cacheKey !== this.state.scope?.cacheKey || detail.key !== sessionId) return
      this.streamCursor = streamCursor(detail.stream_cursor)
      this.lastChunkSeq = this.streamCursor?.stream_seq ?? 0
      this.update({ title: detail.title, messages: canonicalMessages(detail), running: detail.running,
        phase: detail.running ? 'sending' : 'ready', error: '' })
    } catch (error) {
      if (generation === this.generation && loadToken === this.loadToken) this.update({ phase: 'failed', error: String((error as Error).message || error) })
    } finally {
      if (loadToken !== this.loadToken) return
      this.loading = false
      if (this.dirtyDuringLoad && generation === this.generation) {
        this.dirtyDuringLoad = false
        void this.refresh().then(() => this.flushQueuedLiveEvents())
      } else {
        this.flushQueuedLiveEvents()
      }
    }
  }

  private flushQueuedLiveEvents(): void {
    if (this.loading || !this.queuedLiveEvents.length) return
    const queued = this.queuedLiveEvents
    this.queuedLiveEvents = []
    const terminalSnapshot = this.state.phase === 'ready' && !this.state.running
    for (const event of queued) this.applyEvent(event, terminalSnapshot)
  }

  async send(): Promise<void> {
    const submittedDraft = this.state.draft
    const text = submittedDraft.trim()
    if (!this.state.scope || !text || this.submitting || this.state.phase === 'uncertain') return
    this.submitting = true
    const submitToken = ++this.submitToken
    let sessionId = this.state.sessionId
    const scope = this.state.scope
    let generation = this.generation
    const clientTs = new Date().toISOString()
    this.update({ phase: 'sending', error: '' })
    try {
      if (!sessionId) {
        sessionId = await this.create()
        generation = this.generation
      }
      if (generation !== this.generation || scope.cacheKey !== this.state.scope?.cacheKey || sessionId !== this.state.sessionId) return
      const accepted = await gatewayJson<{ ok: boolean; session?: string; queued?: boolean }>(
        '/api/chat?ws=1', { method: 'POST', body: { message: text, session: sessionId, meta: { client_ts: clientTs } } },
      )
      if (!accepted.ok || (accepted.session && accepted.session !== sessionId)) throw new Error('Gideon did not confirm the send')
      if (generation !== this.generation || scope.cacheKey !== this.state.scope?.cacheKey || sessionId !== this.state.sessionId) return
      this.update({ draft: this.state.draft === submittedDraft ? '' : this.state.draft, phase: 'sending', running: true })
      await this.refresh()
    } catch (error) {
      if (generation !== this.generation || scope.cacheKey !== this.state.scope?.cacheKey || sessionId !== this.state.sessionId) return
      if (sessionId && !(error instanceof GatewayError && error.status < 500)) {
        try {
          const detail = await gatewayJson<ChatDetail>(`/api/chat/sessions/${encodeURIComponent(sessionId)}`)
          if (generation !== this.generation || scope.cacheKey !== this.state.scope?.cacheKey || sessionId !== this.state.sessionId) return
          if (receivedPrompt(detail, clientTs)) {
            this.update({ draft: this.state.draft === submittedDraft ? '' : this.state.draft, messages: canonicalMessages(detail),
              running: detail.running, phase: detail.running ? 'sending' : 'ready', error: '' })
            return
          }
        } catch {
          if (generation !== this.generation || scope.cacheKey !== this.state.scope?.cacheKey || sessionId !== this.state.sessionId) return
          this.update({ phase: 'uncertain', error: 'Send outcome is unknown. Refresh the conversation before trying again.' })
          return
        }
      }
      this.update({ draft: this.state.draft || submittedDraft, phase: 'failed', error: String((error as Error).message || error) })
    } finally {
      if (submitToken === this.submitToken) this.submitting = false
    }
  }

  receive(event: ChatSocketEvent): void {
    const { sessionId, scope } = this.state
    const sessionlessApprovalResolution = event.type === 'approval_resolved'
      && event.data.session === undefined
    if (!scope || !sessionId || (event.data.session !== sessionId && !sessionlessApprovalResolution)) return
    if (this.loading) {
      if (event.type === 'chat_user_message' || event.type === 'chat_done'
        || (event.type === 'chat_message' && event.data.role === 'error')) this.dirtyDuringLoad = true
      if (event.type === 'chat_chunk' || event.type === 'tool_call' || event.type === 'tool_result'
        || event.type === 'approval' || event.type === 'approval_resolved'
        || event.type === 'activity_event' || event.type === 'chat_thinking'
        || (event.type === 'chat_message' && event.data.role === 'error')) {
        if (this.queuedLiveEvents.length < 256) this.queuedLiveEvents.push(event)
      }
      return
    }
    this.applyEvent(event)
  }

  private applyEvent(event: ChatSocketEvent, terminalSnapshot = false): void {
    const { sessionId } = this.state
    if (!sessionId) return
    if (event.type === 'chat_chunk') {
      const content = event.data.content
      const seq = event.data.seq
      if (typeof content !== 'string' || typeof seq !== 'number') return
      const incomingCursor = streamCursor(event.data)
      if (incomingCursor) {
        const reconciled = reconcileStreamChunk(sessionId, this.state.messages,
          this.streamCursor, content, incomingCursor)
        if (!reconciled) return
        this.streamCursor = reconciled.cursor
        this.lastChunkSeq = incomingCursor.stream_seq
        this.update({ messages: reconciled.messages, running: true, phase: 'sending' })
        return
      } else {
        if (terminalSnapshot || seq <= this.lastChunkSeq) return
        this.lastChunkSeq = seq
      }
      const messages = [...this.state.messages]
      let streamingIndex = -1
      for (let index = messages.length - 1; index >= 0; index--) {
        if (messages[index].streaming) { streamingIndex = index; break }
      }
      if (streamingIndex >= 0) {
        const streaming = messages[streamingIndex]
        messages[streamingIndex] = { ...streaming, content: streaming.content + content }
      } else messages.push({ id: `${sessionId}:live`, role: 'assistant', content, streaming: true })
      this.update({ messages, running: true, phase: 'sending' })
    } else if (event.type === 'tool_call' || event.type === 'tool_result') {
      const toolCallId = typeof event.data.tool_call_id === 'string' ? event.data.tool_call_id : ''
      const messages = [...this.state.messages]
      const found = toolCallId ? messages.findIndex(message => message.role === 'tool'
        && message.meta?.tool_call_id === toolCallId) : -1
      const previous = found >= 0 ? messages[found] : undefined
      if (previous?.meta?.done === true) return
      const incoming = event.type === 'tool_call' ? {
        ...(previous?.meta ?? {}), ...event.data,
        tool_call_id: toolCallId || undefined,
        done: event.data.update === true ? previous?.meta?.done : false,
      } : {
        ...(previous?.meta ?? {}), ...event.data,
        tool_call_id: toolCallId || undefined,
        done: true,
      }
      const message: ConversationMessage = {
        id: previous?.id ?? `${sessionId}:tool:${toolCallId || messages.length}`,
        role: 'tool', content: typeof event.data.tool === 'string' ? event.data.tool : previous?.content ?? 'Tool',
        meta: incoming,
      }
      if (found >= 0) messages[found] = message
      else messages.push(message)
      this.update(terminalSnapshot ? { messages } : { messages, running: true, phase: 'sending' })
    } else if (event.type === 'approval' || event.type === 'approval_resolved') {
      const approvalId = typeof event.data.id === 'string' ? event.data.id : ''
      const messages = [...this.state.messages]
      const found = approvalId ? messages.findIndex(message => message.role === 'permission'
        && (message.meta?.approval_id === approvalId || message.meta?.id === approvalId)) : -1
      if (event.type === 'approval_resolved' && found < 0) return
      const previous = found >= 0 ? messages[found] : undefined
      const resolved = event.type === 'approval_resolved'
        ? (event.data.approved === true ? String(event.data.decision ?? 'approved') : 'rejected')
        : undefined
      const message: ConversationMessage = {
        id: previous?.id ?? `${sessionId}:approval:${approvalId || messages.length}`,
        role: 'permission', content: typeof event.data.tool === 'string' ? event.data.tool : previous?.content ?? 'Approval request',
        meta: { ...(previous?.meta ?? {}), ...event.data, id: approvalId || undefined,
          approval_id: approvalId || undefined, ...(resolved ? { resolved } : {}) },
      }
      if (found >= 0) messages[found] = message
      else messages.push(message)
      this.update({ messages })
    } else if (event.type === 'activity_event' || event.type === 'chat_thinking') {
      const id = typeof event.data.id === 'string' ? event.data.id
        : typeof event.data.event_id === 'string' ? event.data.event_id
          : `${event.type}:${String(event.data.seq ?? this.state.messages.length)}`
      const messages = [...this.state.messages]
      const role = event.type === 'chat_thinking' ? 'thinking' : 'activity'
      const messageId = `${sessionId}:${role}:${id}`
      const existing = messages.findIndex(message => message.id === messageId)
      const content = typeof event.data.content === 'string' ? event.data.content
        : typeof event.data.text === 'string' ? event.data.text : ''
      if (existing >= 0 && role === 'thinking') {
        const previous = messages[existing]
        messages[existing] = { ...previous, content: previous.content + content }
      } else if (content || event.type === 'activity_event') {
        messages.push({ id: messageId, role, content, meta: event.data })
      }
      this.update({ messages, ...(event.type === 'activity_event' || terminalSnapshot ? {} : { running: true, phase: 'sending' as const }) })
    } else if (event.type === 'chat_user_message' || event.type === 'chat_done') {
      void this.refresh()
    } else if (event.type === 'chat_message' && event.data.role === 'error') {
      void this.refresh()
    }
  }

  private connect(): void {
    const scope = this.state.scope
    if (!scope || this.closed) return
    const generation = this.generation
    const origin = new URL(scope.runtimeOrigin)
    const address = `${origin.protocol === 'https:' ? 'wss:' : 'ws:'}//${origin.host}/api/ws`
    let socket: WebSocket
    try { socket = new WebSocket(address) } catch { this.reconnect(generation); return }
    this.socket = socket
    socket.onopen = () => {
      if (generation !== this.generation || this.socket !== socket) return
      this.update({ connected: true })
      if (this.state.sessionId) void this.refresh()
    }
    socket.onmessage = event => {
      if (generation !== this.generation || this.socket !== socket || typeof event.data !== 'string') return
      try {
        const parsed: unknown = JSON.parse(event.data)
        if (parsed && typeof parsed === 'object' && 'type' in parsed && 'data' in parsed &&
          typeof parsed.type === 'string' && parsed.data && typeof parsed.data === 'object') {
          this.receive(parsed as ChatSocketEvent)
        }
      } catch { return }
    }
    socket.onerror = () => socket.close()
    socket.onclose = () => {
      if (generation !== this.generation || this.socket !== socket) return
      this.socket = null
      this.update({ connected: false, phase: this.state.sessionId ? 'recovering' : this.state.phase })
      this.reconnect(generation)
    }
  }

  private reconnect(generation: number): void {
    if (this.timer || generation !== this.generation || this.closed) return
    this.timer = setTimeout(() => {
      this.timer = null
      if (generation === this.generation) this.connect()
    }, 1000)
  }

  dispose(): void {
    this.closed = true
    this.generation++
    this.clearConnection()
    this.listeners.clear()
  }
}
