
import type { ChatFileChange, SpawnMemoryReceipt } from '../../shared/data/api'

export interface TextSegment { kind: 'text'; text: string }

export interface ToolSegment {
  kind: 'tool'
  id: string
  tool: string
  detail?: string
  toolKind?: string
  input?: string
  inputObj?: unknown
  output?: string
  purpose?: string
  auto?: boolean
  done: boolean
  contentType?: string
  rawRef?: string
  truncated?: boolean
  originalLength?: number
  recoveryHints?: string[]
  agentError?: AgentError
  ok?: boolean
}

export interface AgentError {
  code: string
  what: string
  why: string
  fix: string
  suggestions?: string[]
}

const GUARDRAIL_ERROR_CODES = new Set([
  'ERR_COMPUTER_USE_APP_NOT_ALLOWED',
  'ERR_COMPUTER_USE_SECURE_FIELD',
  'ERR_COMPUTER_USE_UNATTENDED_NOT_GRANTED',
  'ERR_COMPUTER_USE_DISABLED',
])

export function guardrailNoticeForTool(segment: ToolSegment): { code: string; reason: string } | null {
  const error = segment.agentError
  return error && GUARDRAIL_ERROR_CODES.has(error.code)
    ? { code: error.code, reason: error.what }
    : null
}

export interface ApprovalSegment {
  kind: 'approval'
  id: string
  tool: string
  input?: string
  purpose?: string
  risk?: 'safe' | 'caution' | 'destructive'
  toolKind?: string
  canRevise?: boolean

  resolved?: string
}

export interface ActivitySegment {
  kind: 'activity'; text: string; activityKind?: string
  origin?: string
}

export interface ErrorSegment { kind: 'error'; text: string }

export interface ThinkingSegment { kind: 'thinking'; text: string }

export type Segment = TextSegment | ToolSegment | ApprovalSegment | ActivitySegment | ErrorSegment | ThinkingSegment

export const appendThinking = (segs: Segment[], chunk: string): Segment[] => {
  if (!chunk) return segs
  const last = segs[segs.length - 1]
  if (last?.kind === 'thinking') return [...segs.slice(0, -1), { kind: 'thinking', text: last.text + chunk }]
  return [...segs, { kind: 'thinking', text: chunk }]
}

export interface MemoryCitation { n: number; id: string | null; preview?: string }

export interface SkillUsed { name: string; state: string; loaded_tokens: number }

export function skillsUsedLabel(skills: SkillUsed[]): string {
  const n = skills.length
  if (!n) return ''
  return `used ${n} skill${n === 1 ? '' : 's'}`
}

export function skillsUsedTitle(skills: SkillUsed[]): string {
  if (!skills.length) return ''
  const lines = skills.map((s) => {
    const name = s.name || '(unnamed skill)'
    return s.state === 'reduced' ? `${name} — summary only` : name
  })
  return `Skills used this turn:\n${lines.join('\n')}`
}

export function stampActivityOrigin(prev: Segment[], next: Segment[], origin?: string): Segment[] {
  if (!origin || next === prev) return next
  const added = next.find((sg) => sg.kind === 'activity' && !prev.includes(sg))
  if (added) (added as ActivitySegment).origin = origin
  return next
}

export interface LearnedSurface { href: string; label: string }
export function learnedSurface(origin?: string | null): LearnedSurface | null {
  switch (origin) {
    case 'proposal':
      return { href: '#/skills?mode=proposals', label: 'Review in Skill proposals →' }
    case 'lesson':
      return { href: '#/settings/memory?tab=studio', label: 'Review lessons in Memory →' }
    case 'facet':
      return { href: '#/settings/memory?tab=studio', label: 'Manage in Memory →' }
    default:
      return null
  }
}

export interface ChatTurn {
  role: 'user' | 'assistant'
  segments: Segment[]
  ts?: string

  citations?: MemoryCitation[]
  skillsUsed?: SkillUsed[]
  pastes?: { seq: number; lines: number; content: string }[]
  files?: string[]
  optimized?: string
  summary?: string
  variantCount?: number
  variantIdx?: number
  rewound?: { messages: { role: string; content: string; ts?: string }[]; ts?: string }[]
  visibleIndex?: number
  fileChanges?: ChatFileChange[]
}

export const userTurn = (text: string, ts?: string, pastes?: ChatTurn['pastes'], files?: string[], optimized?: string): ChatTurn => ({ role: 'user', segments: [{ kind: 'text', text }], ts, pastes, files: files?.length ? files : undefined, optimized: optimized || undefined })
export const assistantTurn = (text = ''): ChatTurn => ({ role: 'assistant', segments: text ? [{ kind: 'text', text }] : [] })

export function turnText(t: ChatTurn): string {
  return t.segments.filter((s): s is TextSegment => s.kind === 'text').map((s) => s.text).join('\n').trim()
}

export interface SubagentCard {
  id: string
  task: string
  agent: string
  lastTool?: string
  done: boolean
  error?: string | null
  elapsed?: number
  result?: string
  costUsd?: number
  tokens?: number
  memoryReceipt?: SpawnMemoryReceipt
}

export function memoryReceiptLabel(receipt: SpawnMemoryReceipt): string {
  if (receipt.status === 'pending') return 'Memory capture pending'
  if (receipt.status === 'recorded') return `${receipt.count} memory contribution${receipt.count === 1 ? '' : 's'} recorded`
  if (receipt.status === 'no_contribution') return 'No memory contribution'
  return 'Memory capture unavailable'
}

export interface FileEntry { path: string; name: string }
export interface LinkEntry { url: string; label: string }
export interface ChatActivity { files: FileEntry[]; links: LinkEntry[] }

const ACT_FILE_RE = /(?:^|[\s(`'"])((?:~|\/)[\w./\-]+\.\w{1,8}|[\w./\-]+\/[\w./\-]+\.\w{1,8})/g
const ACT_URL_RE = /\bhttps?:\/\/[^\s)<>"'`\]]+/g
const baseNameOf = (p: string) => p.replace(/\/+$/, '').split('/').pop() || p
const DIFF_NOISE = /^(?:[ab]\/|\/dev\/null$)/

export function deriveActivity(turns: ChatTurn[]): ChatActivity {
  const files = new Map<string, FileEntry>()
  const links = new Map<string, LinkEntry>()

  const addFile = (raw: string) => {
    let p = raw.trim().replace(/[).,;:]+$/, '')
    if (!p || DIFF_NOISE.test(p)) return
    p = p.replace(/^[ab]\//, '')
    if (!files.has(p)) files.set(p, { path: p, name: baseNameOf(p) })
  }

  turns.forEach((t) => {
    if (t.role === 'user') {
      return
    }
    for (const seg of t.segments) {
      if (seg.kind === 'tool') {
        for (const src of [seg.input, seg.output, seg.detail]) {
          if (!src) continue
          for (const m of src.matchAll(ACT_FILE_RE)) addFile(m[1])
        }
      } else if (seg.kind === 'text') {
        for (const m of seg.text.matchAll(ACT_FILE_RE)) addFile(m[1])
        for (const m of seg.text.matchAll(ACT_URL_RE)) {
          const url = m[0].replace(/[).,;:]+$/, '')
          if (!links.has(url)) { try { links.set(url, { url, label: new URL(url).hostname.replace(/^www\./, '') }) } catch { links.set(url, { url, label: url }) } }
        }
      }
    }
  })
  return { files: [...files.values()], links: [...links.values()] }
}

export interface HistMsg { role: string; content: string; ts?: string; variants?: { content: string; ts?: string }[]; variant_idx?: number; rewound?: { messages: { role: string; content: string; ts?: string }[]; ts?: string }[]; meta?: { kind?: string; tool_call_id?: string; approval_id?: string; tool_kind?: string; can_revise?: boolean; input?: string; tool_input?: string; purpose?: string; risk?: string; output?: string; done?: boolean; tool?: string; detail?: string; resolved?: string; content_type?: string; raw_ref?: string; truncated?: boolean; original_length?: number; recovery_hints?: string[]; agent_error?: AgentError; ok?: boolean; pastes?: { seq: number; lines: number; content: string }[]; files?: string[]; original?: string; ui_label?: string; summary?: string; memory_citations?: MemoryCitation[]; skills_used?: SkillUsed[]; file_changes?: ChatFileChange[] } }

function recollapsePastes(content: string, pastes: { seq: number; lines: number; content: string }[]): string {
  let out = content
  for (const p of [...pastes].sort((a, b) => b.content.length - a.content.length)) {
    if (p.content) out = out.split(p.content).join(markerForSeq(p.seq))
  }
  return out
}
const markerForSeq = (seq: number) => `[Paste #${seq}]`

function toolName(meta: HistMsg['meta'], content: string): string {
  return (meta?.tool || content || 'tool').replace(/^[\p{Emoji_Presentation}\p{Extended_Pictographic}]+\s*/u, '').trim() || 'tool'
}

export function hydrateTurns(messages: HistMsg[], running = false): ChatTurn[] {
  const turns: ChatTurn[] = []
  const toolIndex = new Map<string, ToolSegment>()
  let lastUserText = ''
  let lastUserTs: string | undefined
  let assistantTextSinceUser = false

  let visible = -1

  const lastAssistant = (): ChatTurn => {
    const t = turns[turns.length - 1]
    if (t && t.role === 'assistant') return t
    const nt = assistantTurn(); turns.push(nt); return nt
  }

  for (const m of messages) {
    if (m.role === 'user') {
      visible += 1
      const text = m.content.trim()
      if (text === lastUserText && !assistantTextSinceUser
        && ((!m.ts && !lastUserTs) || (m.ts && m.ts === lastUserTs))) continue

      const pastes = m.meta?.pastes
      const original = m.meta?.original
      const uiLabel = m.meta?.ui_label
      const primary = uiLabel ?? original ?? m.content
      const display = pastes?.length ? recollapsePastes(primary, pastes) : primary
      const files = Array.isArray(m.meta?.files) ? m.meta!.files : undefined
      const ut = userTurn(display, m.ts, pastes?.length ? pastes : undefined, files, original ? m.content : undefined)
      if (Array.isArray(m.rewound) && m.rewound.length) ut.rewound = m.rewound
      ut.visibleIndex = visible
      turns.push(ut)
      lastUserText = text; lastUserTs = m.ts; assistantTextSinceUser = false
    } else if (m.role === 'assistant' || m.role === 'streaming') {
      visible += 1
      const at = lastAssistant()
      at.visibleIndex = visible
      at.segments.push({ kind: 'text', text: m.content })
      if (m.meta?.summary) at.summary = m.meta.summary
      if (Array.isArray(m.meta?.memory_citations) && m.meta!.memory_citations.length) {
        at.citations = m.meta!.memory_citations
      }
      if (Array.isArray(m.meta?.skills_used) && m.meta!.skills_used.length) {
        at.skillsUsed = m.meta!.skills_used
      }
      if (Array.isArray(m.meta?.file_changes) && m.meta.file_changes.length) {
        at.fileChanges = m.meta.file_changes
      }
      if (Array.isArray(m.variants) && m.variants.length > 1) {
        at.variantCount = m.variants.length
        at.variantIdx = typeof m.variant_idx === 'number' ? m.variant_idx : m.variants.length - 1
      }
      assistantTextSinceUser = true
    } else if (m.role === 'tool') {
      const id = m.meta?.tool_call_id || `auto-${turns.length}-${lastAssistant().segments.length}`
      const existing = toolIndex.get(id)
      if (existing) {
        if (m.meta?.output != null) existing.output = m.meta.output
        if (m.meta?.done) existing.done = true
        if (m.meta?.input) existing.input = m.meta.input
        if (m.meta?.kind) existing.toolKind = m.meta.kind
        if (m.meta?.detail) existing.detail = m.meta.detail
        if (m.meta?.content_type) existing.contentType = m.meta.content_type
        if (m.meta?.raw_ref) existing.rawRef = m.meta.raw_ref
        if (m.meta?.truncated) { existing.truncated = true; existing.originalLength = m.meta.original_length }
        if (m.meta?.recovery_hints?.length) existing.recoveryHints = m.meta.recovery_hints
        if (m.meta?.agent_error) existing.agentError = m.meta.agent_error
        if (m.meta?.ok === false) existing.ok = false
      } else {
        const seg: ToolSegment = { kind: 'tool', id, tool: toolName(m.meta, m.content), toolKind: m.meta?.kind || undefined, detail: m.meta?.detail, input: m.meta?.input, output: m.meta?.output, purpose: m.meta?.purpose, done: !!m.meta?.done, contentType: m.meta?.content_type, rawRef: m.meta?.raw_ref, truncated: m.meta?.truncated, originalLength: m.meta?.original_length, recoveryHints: m.meta?.recovery_hints, agentError: m.meta?.agent_error, ok: m.meta?.ok === false ? false : undefined }
        toolIndex.set(id, seg)
        lastAssistant().segments.push(seg)
      }
    } else if (m.role === 'permission') {
      const resolved = m.meta?.resolved || undefined
      lastAssistant().segments.push({ kind: 'approval', id: m.meta?.approval_id || m.meta?.tool_call_id || `perm-${turns.length}`, tool: toolName(m.meta, m.content), toolKind: m.meta?.tool_kind, canRevise: m.meta?.can_revise === true, input: m.meta?.input || m.meta?.tool_input, purpose: m.meta?.purpose, risk: m.meta?.risk as ApprovalSegment['risk'], resolved })
    } else if (m.role === 'error') {
      lastAssistant().segments.push({ kind: 'error', text: m.content })
    }
  }
  if (!running) {
    for (const seg of toolIndex.values()) seg.done = true
  } else {
    const tools = [...toolIndex.values()]
    tools.slice(0, -1).forEach((seg) => { seg.done = true })
  }
  return turns
}
