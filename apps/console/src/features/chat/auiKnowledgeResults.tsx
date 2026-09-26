import { api, type KnowledgeContextCard, type KnowledgeContextResult, type KnowledgeItem, type ResearchReport, type SemanticEntry } from '../../shared/data/api'
import { paper, field } from '../../shared/vendor/assistant-ui/elements/surfaces'
import { MapAnswer } from '../../shared/vendor/assistant-ui/elements/map-answer'
import { WebSearch } from '../../shared/vendor/assistant-ui/elements/web-search'
import { RetrievalChunks } from '../../shared/vendor/assistant-ui/elements/retrieval-chunks'

export type KnowledgeOpen = (id: string) => void

function safeUrl(value?: string | null): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null
  } catch { return null }
}

export function KnowledgeLinkPreview({ title, url, description }: {
  title: string
  url: string | null | undefined
  description?: string | null
}) {
  const href = safeUrl(url)
  return <div data-slot="link-preview" className={`${paper} rounded-xl p-3`}>
    {href ? <a href={href} target="_blank" rel="noopener noreferrer" className="font-medium underline">{title}</a>
      : <strong>{title}</strong>}
    {href && <p className="break-all text-xs text-on-surface-low">{new URL(href).host}</p>}
    {description && <p className="mt-1 text-sm text-on-surface-var">{description}</p>}
  </div>
}

export function KnowledgeCitation({ card, onOpen }: { card: KnowledgeContextCard; onOpen?: KnowledgeOpen }) {
  const label = card.section ? `${card.title}, ${card.section}` : card.title
  return <span data-slot="inline-citation" className={`${field} inline-flex rounded-md px-2 py-0.5 text-xs`}>
    {onOpen ? <button type="button" onClick={() => onOpen(card.id)} aria-label={`Open source ${label}`}>{label}</button>
      : <span>{label}</span>}
  </span>
}

export function KnowledgeWebSearch({ result, onOpen }: {
  result: KnowledgeContextResult
  onOpen?: KnowledgeOpen
}) {
  const rows = result.results.map((card) => {
    const url = safeUrl(card.deep_link)
    return {
      id: card.id,
      title: card.title,
      domain: url ? new URL(url).host : card.provider || card.source_type || '',
      summary: card.summary,
      url: url || undefined,
    }
  })
  return <section data-slot="web-search" aria-label={`Knowledge search results for ${result.query}`} className="space-y-2">
    <WebSearch query={result.query} results={rows} visibleResults={rows.length} searching={false} cycle={0}
      onSelect={onOpen ? (row) => onOpen(row.id!) : undefined} />
    <div className="flex flex-wrap gap-1" aria-label="Knowledge citations">
      {result.results.map((card) => <KnowledgeCitation key={card.id} card={card} onOpen={onOpen} />)}
    </div>
  </section>
}

export function KnowledgeRetrievalChunks({ result, onOpen }: {
  result: KnowledgeContextResult
  onOpen?: KnowledgeOpen
}) {
  const chunks = result.results.map((card) => ({
    id: card.id,
    source: card.title,
    locator: card.section || '',
    text: card.content || card.summary || 'No passage text available',
    tokens: card.tokens,
    sourceType: card.source_type,
    lineRange: card.line_range,
    deepLink: safeUrl(card.deep_link),
  }))
  return <section data-slot="retrieval-chunks" aria-label="Retrieved passages" className="space-y-2">
    <RetrievalChunks query={result.query} chunks={chunks} visibleCount={chunks.length} searching={false}
      onSelect={onOpen ? (chunk) => onOpen(chunk.id) : undefined} />
    <div className="flex flex-wrap gap-1" aria-label="Knowledge citations">
      {result.results.map((card) => <KnowledgeCitation key={card.id} card={card} onOpen={onOpen} />)}
    </div>
  </section>
}

export function KnowledgeImage({ item, onOpen }: { item: KnowledgeItem; onOpen?: KnowledgeOpen }) {
  const image = item.type === 'image' || item.mime_type?.startsWith('image/')
  const src = image ? (item.file_path ? api.knowledgeItemFileUrl(item.id) : safeUrl(item.url)) : null
  if (!image || !src) return null
  return <figure data-slot="image-generation" className={`${paper} rounded-xl p-2`}>
    <img src={src} alt={item.title || 'Knowledge image'} loading="lazy" className="max-h-72 max-w-full rounded-lg object-contain" />
    <figcaption className="mt-1 text-xs text-on-surface-low">
      {onOpen ? <button type="button" onClick={() => onOpen(item.id)} className="underline">{item.title || 'Open image'}</button>
        : item.title || 'Image'}
    </figcaption>
  </figure>
}

export function KnowledgeImageGallery({ items, onOpen }: { items: readonly KnowledgeItem[]; onOpen?: KnowledgeOpen }) {
  const images = items.filter((item) => (item.type === 'image' || item.mime_type?.startsWith('image/')) && (item.file_path || safeUrl(item.url)))
  if (images.length === 0) return null
  return <section data-slot="image-gallery" aria-label="Knowledge images" className="grid grid-cols-2 gap-2">
    {images.map((item) => <KnowledgeImage key={item.id} item={item} onOpen={onOpen} />)}
  </section>
}

export function KnowledgeDocumentReference({ item, onOpen }: { item: KnowledgeItem; onOpen?: KnowledgeOpen }) {
  return <article data-slot="document-reference" className={`${paper} rounded-xl p-3`}>
    {onOpen ? <button type="button" onClick={() => onOpen(item.id)} className="font-medium underline">{item.title || item.id}</button>
      : <strong>{item.title || item.id}</strong>}
    {item.summary && <p className="mt-1 text-sm">{item.summary}</p>}
    {item.provider && <small className="text-on-surface-low">Source: {item.provider}</small>}
  </article>
}

export function KnowledgeMemoryEntry({ entry }: { entry: SemanticEntry }) {
  return <article data-slot="memory" className={`${paper} rounded-xl p-3`}>
    <strong>{entry.key}</strong>
    {entry.value_json && <p className="mt-1 whitespace-pre-wrap text-sm">{entry.value_json}</p>}
    {entry.source && <small className="text-on-surface-low">Source: {entry.source}</small>}
  </article>
}

export function KnowledgeResearchReport({ report, onRun }: { report: ResearchReport; onRun?: (id: string) => void }) {
  return <article data-slot="research-report" className={`${paper} rounded-xl p-3`}>
    <h3 className="font-medium">{report.name}</h3>
    <p className="text-sm">{report.prompt}</p>
    <p className="text-xs text-on-surface-low">Last run: {report.last_run_ts == null ? 'Never' : new Date(report.last_run_ts * 1000).toLocaleString()} · {report.last_status || 'No status'}</p>
    {report.last_error && <p role="alert">{report.last_error}</p>}
    {onRun && <button type="button" onClick={() => onRun(report.id)} className="mt-2 underline">Run report</button>}
  </article>
}

export function KnowledgeRelationMap({ item, onOpen }: { item: KnowledgeItem; onOpen?: KnowledgeOpen }) {
  return <section data-slot="map" aria-label={`Knowledge relations for ${item.title || item.id}`} className={`${paper} rounded-xl p-3`}>
    <h3>{item.title || item.id}</h3>
    <ul className="mt-2 space-y-1 text-sm">
      {(item.relations || []).map((relation) => <li key={relation.id}>
        {relation.source_name || 'Unknown source'} → {relation.target_name || 'Unknown target'}
        {relation.relation_type && <span> · {relation.relation_type}</span>}
      </li>)}
    </ul>
    {onOpen && <button type="button" onClick={() => onOpen(item.id)} className="mt-2 underline">Open knowledge graph</button>}
  </section>
}

export interface KnowledgeGeoPoint { id: string; label: string; latitude: number; longitude: number }

export function KnowledgeGeoMap({ points, onOpen }: { points: readonly KnowledgeGeoPoint[]; onOpen?: KnowledgeOpen }) {
  const located = points.filter((point) => Number.isFinite(point.latitude) && Number.isFinite(point.longitude)
    && Math.abs(point.latitude) <= 90 && Math.abs(point.longitude) <= 180)
  if (located.length === 0) return null
  return <section data-slot="geo-map" aria-label="Recorded locations" className={`${paper} rounded-xl p-3`}>
    <MapAnswer pins={located.map((point) => ({ id: point.id, label: point.label,
      detail: `${point.latitude}, ${point.longitude}`, x: (point.longitude + 180) / 3.6, y: (90 - point.latitude) / 1.8 }))}
      activeId="" onSelect={onOpen} />
  </section>
}

export function KnowledgeMediaPlayer({ item }: { item: KnowledgeItem }) {
  const kind = item.type === 'audio' || item.mime_type?.startsWith('audio/') ? 'audio'
    : item.type === 'video' || item.mime_type?.startsWith('video/') ? 'video' : null
  const src = item.file_path ? api.knowledgeItemFileUrl(item.id) : safeUrl(item.url)
  if (!kind || !src) return null
  return <figure data-slot="media-player" className={`${paper} rounded-xl p-3`}>
    {kind === 'audio' ? <audio src={src} controls preload="none" aria-label={item.title || 'Knowledge audio'} />
      : <video src={src} controls preload="none" className="max-h-80 w-full" aria-label={item.title || 'Knowledge video'} />}
    <figcaption className="text-xs text-on-surface-low">{item.title || item.id}</figcaption>
  </figure>
}

export function KnowledgeSources({ result, onOpen }: { result: KnowledgeContextResult; onOpen?: KnowledgeOpen }) {
  return <section data-slot="sources" aria-label="Knowledge sources" className={`${paper} rounded-xl p-3`}>
    <h3>{result.results.length} sources</h3>
    <ol className="mt-2 space-y-1">{result.results.map((card) => <li key={card.id}>
      <KnowledgeCitation card={card} onOpen={onOpen} />
    </li>)}</ol>
  </section>
}
