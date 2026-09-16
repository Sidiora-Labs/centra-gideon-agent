import { api } from '../../data/api'

export interface MentionRow { kind: 'file' | 'knowledge' | 'prompt'; id: string; name: string; sub: string; size?: number }
export class ComposerSearchCache<T> {
  private entries = new Map<string, { at: number; value: T }>()
  private pending = new Map<string, Promise<T>>()
  constructor(private ttl = 30000, private capacity = 80) {}
  peek(key: string, now = performance.now()): T | undefined {
    const found = this.entries.get(key)
    if (!found) return undefined
    if (now - found.at >= this.ttl) { this.entries.delete(key); return undefined }
    this.entries.delete(key); this.entries.set(key, found)
    return found.value
  }
  async load(key: string, read: () => Promise<T>): Promise<T> {
    const cached = this.peek(key)
    if (cached !== undefined) return cached
    const request = this.pending.get(key)
    if (request) return request
    const next = read().then(value => {
      this.entries.set(key, { at: performance.now(), value })
      while (this.entries.size > this.capacity) this.entries.delete(this.entries.keys().next().value!)
      return value
    }).finally(() => this.pending.delete(key))
    this.pending.set(key, next)
    return next
  }
}

export const mentionResults = new ComposerSearchCache<MentionRow[]>()
export function mentionSearchKey(query: string, project?: string, leading?: boolean) {
  return JSON.stringify([project ?? '', leading === true, query.toLowerCase()])
}
export function formatMentionSize(bytes: number) {
  if (bytes < 1024) return `${bytes}B`
  const megabytes = bytes / 1048576
  return megabytes < 1 ? `${(bytes / 1024).toFixed(0)}KB` : `${megabytes.toFixed(1)}MB`
}
export function searchMentions(query: string, project?: string, leading?: boolean) {
  return mentionResults.load(mentionSearchKey(query, project, leading), async () => {
    const [files, knowledge, prompts] = await Promise.all([
      api.fileSearch(query, project).then(result => result.results ?? []).catch(() => []),
      api.knowledgeItems({ q: query, limit: 6 }).then(result => result.items ?? []).catch(() => []),
      leading ? api.prompts('user').catch(() => []) : Promise.resolve([]),
    ])
    const needle = query.toLowerCase()
    return [
      ...prompts.filter(prompt => `${prompt.name} ${prompt.title ?? ''}`.toLowerCase().includes(needle)).slice(0, 6)
        .map((prompt): MentionRow => ({ kind: 'prompt', id: prompt.name, name: prompt.name, sub: prompt.title || prompt.description || 'prompt' })),
      ...files.map((file): MentionRow => ({ kind: 'file', id: file.path, name: file.name, sub: file.path, size: file.size })),
      ...knowledge.map((item): MentionRow => ({ kind: 'knowledge', id: item.id, name: item.title || 'Untitled', sub: item.item_type || 'knowledge' })),
    ]
  })
}
