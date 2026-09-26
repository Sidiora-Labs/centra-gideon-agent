import { useEffect, useState } from 'react'
import { api, type KnowledgeContextResult } from '../../shared/data/api'
import { KnowledgeRetrievalChunks, KnowledgeWebSearch, type KnowledgeOpen } from '../chat/auiKnowledgeResults'

export function splitKnowledgeResults(result: KnowledgeContextResult) {
  const web = result.results.filter((card) => card.source_type === 'bookmark' || card.source_type === 'web_url')
  const other = result.results.filter((card) => card.source_type !== 'bookmark' && card.source_type !== 'web_url')
  return { web, other }
}

export function AuiKnowledgePanel({ query, onOpen }: { query: string; onOpen?: KnowledgeOpen }) {
  const [result, setResult] = useState<KnowledgeContextResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const search = query.trim()
    if (!search) { setResult(null); setError(null); setLoading(false); return }
    let active = true
    setLoading(true)
    setError(null)
    api.knowledgeSearchForContext(search).then((next) => {
      if (active) setResult(next)
    }).catch((reason: unknown) => {
      if (active) { setResult(null); setError(reason instanceof Error ? reason.message : 'Search failed') }
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [query])

  const { web, other } = result ? splitKnowledgeResults(result) : { web: [], other: [] }

  return <section aria-label="Knowledge search" className="space-y-3">
    {loading && <p role="status">Searching knowledge…</p>}
    {error && <p role="alert">{error}</p>}
    {!loading && !error && result && <>
      {web.length > 0 && <KnowledgeWebSearch result={{ ...result, results: web }} onOpen={onOpen} />}
      {other.length > 0 && <KnowledgeRetrievalChunks result={{ ...result, results: other }} onOpen={onOpen} />}
      {result.results.length === 0 && <p>No matching knowledge records.</p>}
    </>}
  </section>
}
