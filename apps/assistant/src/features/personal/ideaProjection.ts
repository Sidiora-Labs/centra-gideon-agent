import type { IdeaRecord, PersonalRecord } from './client'

export type ProjectedIdea = Readonly<{
  listId: string
  listTitle: string
  memberId: string
  title: string
  evidence: string
  reason: string
  sourceKind: 'knowledge-idea-list'
  sourceId: string
  sourceLink: string
  freshness: 'current' | 'stale'
  revision?: number
  revisionHash?: string
  sourceStatus: string
}>

export type SuggestionPrompt = Readonly<{ text: string; generatedAt: number; stale: boolean }>

function nonempty(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

export function projectIdeas(records: readonly PersonalRecord<IdeaRecord>[]): readonly ProjectedIdea[] {
  return records.flatMap(record => {
    const list = record.value
    const document = list.document && typeof list.document === 'object' ? list.document as Record<string, unknown> : {}
    const members = Array.isArray(list.items) ? list.items : []
    const listTitle = nonempty(list.title) ?? nonempty(document.title) ?? 'Saved idea list'
    const reason = nonempty(document.prompt) ?? nonempty(document.help) ?? 'Saved in a native Gideon idea list.'
    const sourceLink = nonempty(list.source_link) ?? `#/knowledge/item/${encodeURIComponent(record.identity.nativeId)}`
    const status = nonempty(list.status) ?? 'unknown'
    return members.flatMap((member: unknown) => {
      if (!member || typeof member !== 'object') return []
      const item = member as Record<string, unknown>
      const memberId = nonempty(item.id)
      const evidence = nonempty(item.content)
      if (!memberId || !evidence) return []
      return [{
        listId: record.identity.nativeId,
        listTitle,
        memberId,
        title: nonempty(item.title) ?? evidence.slice(0, 100),
        evidence,
        reason,
        sourceKind: 'knowledge-idea-list' as const,
        sourceId: memberId,
        sourceLink: nonempty(item.source_link) ?? sourceLink,
        freshness: record.freshness,
        ...(typeof list.revision === 'number' ? { revision: list.revision } : {}),
        ...(nonempty(list.hash) ? { revisionHash: nonempty(list.hash) } : {}),
        sourceStatus: status,
      }]
    })
  })
}

export function projectSuggestionPrompts(payload: unknown): readonly SuggestionPrompt[] {
  if (!payload || typeof payload !== 'object') return []
  const value = payload as Record<string, unknown>
  if (!Array.isArray(value.suggestions)) return []
  const generatedAt = typeof value.generated_at === 'number' ? value.generated_at : 0
  const stale = value.stale === true || generatedAt === 0
  return value.suggestions.flatMap((entry: unknown) => typeof entry === 'string' && entry.trim()
    ? [{ text: entry.trim(), generatedAt, stale }]
    : [])
}
