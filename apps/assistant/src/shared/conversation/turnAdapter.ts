import type { ConversationMessage } from './types'

export type NativeResultRecord = Readonly<{ kind: 'task' | 'artifact' | 'project' | 'chat_session'; id: string }>

type SegmentBase = Readonly<{
  id: string
  producerId?: string
  producerKind?: string
  sourceId?: string
  status?: string
}>

export type ConversationSegment =
  | (SegmentBase & Readonly<{ kind: 'text'; text: string }>)
  | (SegmentBase & Readonly<{ kind: 'thinking'; text: string }>)
  | (SegmentBase & Readonly<{ kind: 'tool'; tool: string; detail?: string; input?: string; output?: string; lifecycle: 'running' | 'succeeded' | 'failed' | 'unknown' }>)
  | (SegmentBase & Readonly<{ kind: 'activity'; text: string; activityKind?: string }>)
  | (SegmentBase & Readonly<{ kind: 'approval'; tool: string; input?: string; purpose?: string; resolved?: string }>)
  | (SegmentBase & Readonly<{ kind: 'error'; text: string }>)
  | (SegmentBase & Readonly<{ kind: 'citation'; label: string; excerpt?: string }>)
  | (SegmentBase & Readonly<{ kind: 'file'; name: string; mediaType?: string }>)
  | (SegmentBase & Readonly<{ kind: 'result'; title: string; summary?: string; record?: NativeResultRecord }>)

export type AdaptedTurn = Readonly<{ messageId: string; role: string; segments: readonly ConversationSegment[] }>

type Meta = Readonly<Record<string, unknown>>

function record(value: unknown): Meta | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Meta : undefined
}

function string(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function idFor(message: ConversationMessage, suffix: string, index = 0): string {
  return `${message.id}:${suffix}:${index}`
}

function base(meta: Meta | undefined, id: string): SegmentBase {
  return {
    id,
    producerId: string(meta?.producer_id) ?? string(meta?.producerId),
    producerKind: string(meta?.producer_kind) ?? string(meta?.producerKind),
    sourceId: string(meta?.source_id) ?? string(meta?.sourceId),
    status: string(meta?.status),
  }
}

function nativeRecord(value: unknown): NativeResultRecord | undefined {
  const candidate = record(value)
  const kind = string(candidate?.kind)
  const id = string(candidate?.id)
  if (!id || (kind !== 'task' && kind !== 'artifact' && kind !== 'project' && kind !== 'chat_session')) return undefined
  return { kind, id }
}

function resultSegment(value: unknown, message: ConversationMessage, index: number): ConversationSegment | undefined {
  const item = record(value)
  if (!item) return undefined
  const resultId = string(item.result_id) ?? string(item.id)
  const title = string(item.title) ?? string(item.name) ?? string(item.label)
  if (!title && !resultId) return undefined
  const meta = { ...item, producer_id: item.producer_id, producer_kind: item.producer_kind, source_id: item.source_id }
  const target = nativeRecord(item.record) ?? nativeRecord({ kind: item.record_kind ?? item.target_kind, id: item.record_id ?? item.target_id })
  return {
    ...base(meta, resultId ?? idFor(message, 'result', index)),
    kind: 'result',
    title: title ?? 'Structured result',
    summary: string(item.summary) ?? string(item.description),
    ...(target ? { record: target } : {}),
  }
}

function metadataSegments(message: ConversationMessage, meta: Meta | undefined): ConversationSegment[] {
  if (!meta) return []
  const segments: ConversationSegment[] = []
  const typed = Array.isArray(meta.segments) ? meta.segments : []
  for (const [index, value] of typed.entries()) {
    const item = record(value)
    if (!item || typeof item.kind !== 'string') continue
    const id = string(item.id) ?? idFor(message, item.kind, index)
    const common = base(item, id)
    switch (item.kind) {
      case 'thinking': if (string(item.text)) segments.push({ ...common, kind: 'thinking', text: string(item.text)! }); break
      case 'activity': if (string(item.text)) segments.push({ ...common, kind: 'activity', text: string(item.text)!, activityKind: string(item.activity_kind) ?? string(item.activityKind) }); break
      case 'citation': {
        const label = string(item.label) ?? string(item.source_label) ?? string(item.source_id)
        if (label) segments.push({ ...common, kind: 'citation', label, excerpt: string(item.preview) ?? string(item.excerpt) })
        break
      }
      case 'file': {
        const name = string(item.name) ?? string(item.path) ?? string(item.file_name)
        if (name) segments.push({ ...common, kind: 'file', name, mediaType: string(item.content_type) ?? string(item.media_type) })
        break
      }
      case 'result': {
        const result = resultSegment(item, message, index)
        if (result) segments.push(result)
        break
      }
    }
  }
  const results = Array.isArray(meta.results) ? meta.results : meta.result ? [meta.result] : []
  for (const [index, value] of results.entries()) {
    const result = resultSegment(value, message, index)
    if (result) segments.push(result)
  }
  const citations = Array.isArray(meta.memory_citations) ? meta.memory_citations : Array.isArray(meta.citations) ? meta.citations : []
  for (const [index, value] of citations.entries()) {
    const item = record(value)
    const label = string(item?.source_label) ?? string(item?.label) ?? string(item?.id)
    if (item && label) segments.push({ ...base(item, string(item.id) ?? idFor(message, 'citation', index)), kind: 'citation', label, excerpt: string(item.preview) })
  }
  const files = Array.isArray(meta.files) ? meta.files : []
  for (const [index, value] of files.entries()) {
    const item = record(value)
    const name = typeof value === 'string' ? value : string(item?.name) ?? string(item?.path)
    if (name) segments.push({ ...base(item, string(item?.id) ?? idFor(message, 'file', index)), kind: 'file', name, mediaType: string(item?.content_type) })
  }
  return segments
}

function toolLifecycle(meta: Meta): 'running' | 'succeeded' | 'failed' | 'unknown' {
  if (meta.done === false) return 'running'
  if (meta.ok === false || meta.status === 'failed' || meta.status === 'error') return 'failed'
  if (meta.done === true || meta.status === 'succeeded' || meta.status === 'complete' || meta.status === 'completed') return 'succeeded'
  return 'unknown'
}

export function adaptConversationMessage(message: ConversationMessage): AdaptedTurn {
  const meta = record(message.meta)
  const segments: ConversationSegment[] = []
  if ((message.role === 'assistant' || message.role === 'streaming') && message.content) {
    segments.push({ ...base(meta, idFor(message, 'text')), kind: 'text', text: message.content })
  }
  if (message.role === 'thinking' && message.content) {
    segments.push({ ...base(meta, string(meta?.id) ?? idFor(message, 'thinking')), kind: 'thinking', text: message.content })
  }
  const detail = string(meta?.detail)
  const tool = string(meta?.tool) ?? (message.role === 'tool' ? message.content : undefined)
  if (tool || message.role === 'tool_call' || message.role === 'tool_result') {
    const toolMeta = meta ?? {}
    segments.push({ ...base(toolMeta, string(meta?.tool_call_id) ?? string(meta?.id) ?? idFor(message, 'tool')),
      kind: 'tool', tool: tool ?? 'Unknown tool', detail,
      input: string(meta?.input_preview) ?? string(meta?.input) ?? string(meta?.tool_input),
      output: string(meta?.output), lifecycle: toolLifecycle(toolMeta) })
  }
  if (message.role === 'permission' || message.role === 'approval') {
    const approvalId = string(meta?.approval_id) ?? string(meta?.id) ?? string(meta?.tool_call_id) ?? idFor(message, 'approval')
    segments.push({ ...base(meta, approvalId), kind: 'approval', tool: tool ?? 'Approval request',
      input: string(meta?.input) ?? string(meta?.tool_input), purpose: string(meta?.purpose) ?? string(meta?.tool_purpose),
      resolved: string(meta?.resolved) ?? string(meta?.decision) })
  }
  if (message.role === 'error' || meta?.kind === 'error') {
    segments.push({ ...base(meta, string(meta?.id) ?? idFor(message, 'error')), kind: 'error', text: message.content })
  }
  if (message.role === 'activity' || message.role === 'activity_event') {
    segments.push({ ...base(meta, string(meta?.id) ?? idFor(message, 'activity')), kind: 'activity',
      text: string(meta?.text) ?? message.content, activityKind: string(meta?.kind) })
  }
  if (message.role === 'result') {
    const result = resultSegment(meta, message, 0)
    if (result) segments.push(result)
  }
  segments.push(...metadataSegments(message, meta))
  return { messageId: message.id, role: message.role, segments }
}

export function adaptConversationMessages(messages: readonly ConversationMessage[]): readonly AdaptedTurn[] {
  return messages.map(adaptConversationMessage)
}
