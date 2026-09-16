import { useEffect, useRef, useState } from 'react'
import { api, type PromptItem, type PromptSnippet } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
export type PromptLibraryRow = PromptItem & PromptSnippet
export function selectPromptRows(rows: PromptLibraryRow[], query: string, sort: string, source: string): PromptLibraryRow[] {
  const needle = query.trim().toLowerCase()
  const compareName = (a: PromptLibraryRow, b: PromptLibraryRow) => (a.title || a.name).localeCompare(b.title || b.name)
  const comparisons: Record<string, (a: PromptLibraryRow, b: PromptLibraryRow) => number> = {
    updated: (a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0),
    source: (a, b) => (a.source || 'user').localeCompare(b.source || 'user'),
    vars: (a, b) => (b.variables?.length ?? 0) - (a.variables?.length ?? 0),
  }
  return rows.filter(row => (source === 'all' || (row.source || 'user') === source) && (!needle || [row.name, row.title, row.description, ...(row.tags ?? [])].filter(Boolean).join(' ').toLowerCase().includes(needle))).sort((a, b) => (comparisons[sort]?.(a, b) ?? 0) || compareName(a, b))
}
export function usePromptLibrary() {
  const prompts = useQuery<PromptItem[]>('prompts', () => api.prompts(), { persist: true })
  const snippets = useQuery<PromptSnippet[]>('prompt-snippets', () => api.snippets(), { persist: true })
  const load = () => { for (const key of ['prompts', 'prompt-snippets']) invalidateKeys(key); prompts.refresh(); snippets.refresh() }
  return { items: prompts.data, snippets: snippets.data, itemsErr: prompts.error, snipsErr: snippets.error, load }
}
export function usePromptRecord(snippet: boolean, name: string) {
  const [record, setRecord] = useState<PromptItem | PromptSnippet | null | 'missing'>(null)
  const [revision, refresh] = useState(0)
  const identity = `${snippet ? 'snippet' : 'prompt'}:${name}`
  const previousIdentity = useRef(identity)
  useEffect(() => {
    let current = true
    if (previousIdentity.current !== identity) { previousIdentity.current = identity; setRecord(null) }
    const request = snippet ? api.snippet(name) : api.prompt(name)
    request.then(value => { if (current) setRecord(value) }).catch(() => { if (current) setRecord('missing') })
    return () => { current = false }
  }, [snippet, name, identity, revision])
  return { record, refresh: () => refresh(value => value + 1) }
}
