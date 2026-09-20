import { useSyncExternalStore } from 'react'

export interface DocComment {
  id: string
  docId: string
  docLabel: string
  docPath?: string
  quote: string
  comment: string
  line?: number
  column?: number
  context?: string
  ts: number
}

export function findCoords(content: string, selected: string): { line: number; column: number } | undefined {
  if (!selected) return undefined
  const idx = content.indexOf(selected)
  if (idx < 0) return undefined
  const before = content.slice(0, idx)
  const nl = before.lastIndexOf('\n')
  return { line: (before.match(/\n/g)?.length ?? 0) + 1, column: (nl < 0 ? idx : idx - nl - 1) + 1 }
}

export function captureContext(content: string, quote: string, line?: number, column?: number): string | undefined {
  if (!content || line == null || column == null) return undefined
  const lines = content.split('\n')
  if (line < 1 || line > lines.length) return undefined
  const ln = lines[line - 1]
  const start = Math.max(0, column - 1 - 20)
  const end = Math.min(ln.length, column - 1 + quote.length + 20)
  return `${start > 0 ? '…' : ''}${ln.slice(start, end)}${end < ln.length ? '…' : ''}`
}

export function formatCommentsMessage(comments: DocComment[], instructions: string): string {
  const esc = (s: string) => s.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
  const byDoc = new Map<string, DocComment[]>()
  for (const c of comments) { const arr = byDoc.get(c.docId) ?? []; arr.push(c); byDoc.set(c.docId, arr) }
  const out: string[] = []
  if (instructions) out.push(instructions, '')
  for (const [, list] of byDoc) {
    const label = list[0].docLabel || list[0].docId
    out.push(`[Document feedback on ${label} — ${list.length} comment${list.length === 1 ? '' : 's'}]`, '')
    list.forEach((c, i) => {
      const anchor = c.quote.length > 80 ? c.quote.slice(0, 80) + '…' : c.quote
      const loc = c.line != null ? (c.column != null ? `line ${c.line}, col ${c.column}, ` : `line ${c.line}, `) : ''
      const ctx = c.context ? ` in "${esc(c.context)}"` : ''
      out.push(`${i + 1}. (${loc}"${esc(anchor)}"${ctx}): "${esc(c.comment)}"`)
    })
    out.push('')
  }
  return out.join('\n').trim()
}

type NewComment = Omit<DocComment, 'id' | 'ts'>
let comments: DocComment[] = []
let error: string | undefined
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((listener) => listener())
const fromServer = (comment: DocComment): DocComment => ({ ...comment, ts: comment.ts * 1000 })

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { credentials: 'same-origin', ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || `Request failed (${response.status})`)
  return response.json()
}

async function resync(): Promise<void> {
  try {
    const data = await request<{ comments: DocComment[] }>('/api/doc-comments')
    comments = data.comments.map(fromServer)
    error = undefined
  } catch (cause) {
    error = cause instanceof Error ? cause.message : 'Could not load comments'
  }
  emit()
}

async function write(action: () => Promise<void>): Promise<void> {
  try { await action(); error = undefined } catch (cause) {
    const writeError = cause instanceof Error ? cause.message : 'Could not save comments'
    await resync()
    error = writeError
  }
  emit()
}

export const commentStore = {
  all: (): DocComment[] => comments,
  error: (): string | undefined => error,
  resync,
  add(c: NewComment): Promise<void> {
    return write(async () => {
      const data = await request<{ comment: DocComment }>('/api/doc-comments', { method: 'POST', body: JSON.stringify(c) })
      comments = [...comments, fromServer(data.comment)]
    })
  },
  update(id: string, patch: Pick<DocComment, 'comment'>): Promise<void> {
    return write(async () => {
      const data = await request<{ comment: DocComment }>(`/api/doc-comments/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(patch) })
      comments = comments.map((comment) => comment.id === id ? fromServer(data.comment) : comment)
    })
  },
  remove(id: string): Promise<void> {
    return write(async () => {
      await request(`/api/doc-comments/${encodeURIComponent(id)}`, { method: 'DELETE' })
      comments = comments.filter((comment) => comment.id !== id)
    })
  },
  removeMany(ids: string[]): Promise<void> {
    return write(async () => {
      await request('/api/doc-comments', { method: 'DELETE', body: JSON.stringify({ ids }) })
      const removed = new Set(ids); comments = comments.filter((comment) => !removed.has(comment.id))
    })
  },
  clear(): Promise<void> { return this.removeMany(comments.map((comment) => comment.id)) },
  subscribe(fn: () => void): () => void { listeners.add(fn); return () => { listeners.delete(fn) } },
}

void resync()

export function useComments(): DocComment[] {
  return useSyncExternalStore(commentStore.subscribe, commentStore.all, commentStore.all)
}
