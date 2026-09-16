import { useCallback, useEffect, useState } from 'react'
import { MoreRow } from '../../shared/ui/MoreRow'
import { ArrowLeft, Network, Layers, Highlighter, Copy } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { PageTitle } from '../../shared/ui/PageTitle'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { LoadError } from '../../shared/ui/ListScaffold'
import { SidePanel } from '../../shared/ui/SidePanel'
import { IconButton } from '../../shared/ui/IconButton'
import { Markdown } from '../../shared/ui/Markdown'
import { KnowledgeDetail } from './KnowledgeDetail'
import { AnnotationList } from './ReadingView'
import { DuplicateList } from './DuplicateList'
import { KnowledgeEgoGraph, type KnowledgeGraphPayload } from './KnowledgeEgoGraph'
import { Button } from '../../shared/ui/Button'
import { resolveType, typeLabel } from './knowledgeMeta'
import { api, type KnowledgeAnnotation, type KnowledgeDuplicate, type KnowledgeItem, type ExtractedContent, ApiError } from '../../shared/data/api'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'

const canvasInk = (tone: string) => (tone === 'var(--color-primary)' ? 'var(--color-primary-emphasis)' : tone)

export function KnowledgeDetailPage({ id, onBack, onOpenItem, query, setQuery }: {
  id: string
  onBack: () => void
  onOpenItem: (id: string) => void
  query: RouteProps['query']
  setQuery: RouteProps['setQuery']
}) {
  const [item, setItem] = useState<KnowledgeItem | null>(null)
  const [missing, setMissing] = useState(false)
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const [detailsParam, setDetailsParam] = useQueryParam(query, setQuery, 'details', '')
  const showDetails = detailsParam === '1'
  const setShowDetails = (v: boolean) => setDetailsParam(v ? '1' : '')
  const [readParam, setReadParam] = useQueryParam(query, setQuery, 'read', '')
  const reading = readParam === '1'
  const toggleReading = useCallback(() => setReadParam(reading ? '' : '1'), [reading, setReadParam])
  const [pool, setPool] = useState<ExtractedContent[]>([])
  const [related, setRelated] = useState<KnowledgeItem[]>([])
  const [annotations, setAnnotations] = useState<KnowledgeAnnotation[]>([])
  const [duplicates, setDuplicates] = useState<KnowledgeDuplicate[]>([])
  const [duplicatesErr, setDuplicatesErr] = useState<unknown>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [header, setHeader] = useState<{ wand: React.ReactNode; actions: React.ReactNode; editing: boolean } | null>(null)

  useEffect(() => {
    let alive = true
    setItem(null); setMissing(false); setLoadErr(null)
    api.knowledgeItem(id)
      .then((d) => { if (!alive) return; if (d) setItem(d); else setMissing(true) })
      .catch((e) => {
        if (!alive) return
        if (e instanceof ApiError && e.status === 404) setMissing(true)
        else setLoadErr(e)
      })
    api.knowledgeExtracted(id).then((d) => { if (alive) setPool(d.contents || []) }).catch(() => {})
    api.knowledgeItemRelated(id).then((r) => { if (alive) setRelated(r) }).catch(() => {})
    return () => { alive = false }
  }, [id, reloadKey])

  const [annotationKey, setAnnotationKey] = useState(0)
  const reloadAnnotations = useCallback(() => setAnnotationKey((k) => k + 1), [])
  useEffect(() => {
    let alive = true
    api.knowledgeAnnotations(id).then((a) => { if (alive) setAnnotations(a) }).catch(() => {})
    return () => { alive = false }
  }, [id, annotationKey])

  const removeAnnotation = useCallback(async (annotationId: string) => {
    await api.deleteKnowledgeAnnotation(annotationId).catch(() => {})
    reloadAnnotations()
  }, [reloadAnnotations])

  const [duplicateKey, setDuplicateKey] = useState(0)
  const reloadDuplicates = useCallback(() => setDuplicateKey((k) => k + 1), [])
  useEffect(() => {
    let alive = true
    api.knowledgeDuplicates(id)
      .then((d) => { if (alive) { setDuplicates(d); setDuplicatesErr(null) } })
      .catch((e) => { if (alive) { setDuplicates([]); setDuplicatesErr(e) } })
    return () => { alive = false }
  }, [id, duplicateKey])

  const afterMerge = useCallback(() => {
    setReloadKey((k) => k + 1)
    setAnnotationKey((k) => k + 1)
    reloadDuplicates()
  }, [reloadDuplicates])

  const detailsCount =
    pool.length + (item?.entities?.length ?? 0) + (item?.relations?.length ?? 0) + related.length
    + annotations.length + duplicates.length
  const tm = item ? resolveType(item) : null

  return (
    <WorkbenchLayout
      scroll={false}
      topBar={
        <TopBar
          keepCornerPadding
          contentAligned
          left={
            <div className="flex items-center gap-s min-w-0">
              <IconButton icon={ArrowLeft} label="Back to Knowledge" size={40} onClick={onBack} />
              {
}
              {!header?.editing && (
                <div className="flex items-center gap-s min-w-0 overflow-hidden">
                  <button type="button" onClick={onBack} data-type="body-m" className="text-on-surface-low hover:text-on-surface transition-colors whitespace-nowrap shrink-0">Knowledge</button>
                  <span className="text-on-surface-low shrink-0">/</span>
                  {
}
                  {tm && item && <span data-type="body-s" className="shrink-0 inline-flex items-center gap-1.5 whitespace-nowrap" style={{ color: canvasInk(tm.tone) }}><tm.icon size={16} /> {typeLabel(item)}</span>}
                  {
}
                  <PageTitle className="truncate min-w-0">{item?.title || item?.url_title || (missing ? 'Not found' : loadErr ? "Couldn't load" : 'Loading…')}</PageTitle>
                </div>
              )}
              { }
              {header?.wand}
            </div>
          }
          right={header?.actions}
        />
      }
      panel={
        showDetails && item ? (
          <SidePanel fillHeight storeKey="knowledge-extras-w" icon={<Layers size={18} className="text-primary" />} title="More details" onClose={() => setShowDetails(false)}>
            <KnowledgeExtras item={item} pool={pool} related={related} onOpenItem={onOpenItem}
              annotations={annotations} onRemoveAnnotation={removeAnnotation}
              duplicates={duplicates} duplicatesError={duplicatesErr}
              onRetryDuplicates={reloadDuplicates} onMerged={afterMerge} />
          </SidePanel>
        ) : undefined
      }
    >
      {
}
      <div className="mx-auto flex h-full min-h-0 w-full flex-col px-l pt-l" style={{ maxWidth: 'var(--content-width)' }}>
        {loadErr ? (
          <LoadError what="knowledge item" error={loadErr} onRetry={() => setReloadKey((k) => k + 1)} />
        ) : missing ? (
          <div data-type="body-s" className="grid h-full place-items-center text-on-surface-low">This knowledge item no longer exists.</div>
        ) : item ? (
          <KnowledgeDetail
            item={item}
            detailsCount={detailsCount}
            detailsOpen={showDetails}
            onShowDetails={() => setShowDetails(!showDetails)}
            onHeader={setHeader}
            onChanged={() => setReloadKey((k) => k + 1)}
            onDeleted={onBack}
            onTagClick={() => onBack()}
            reading={reading}
            onToggleReading={toggleReading}
            annotations={annotations}
            onAnnotationsChanged={reloadAnnotations}
            insightRail={hasReaderInsights(item, related, annotations) ? (
              <ReaderInsights item={item} related={related} annotations={annotations}
                onRemoveAnnotation={removeAnnotation} onOpenItem={onOpenItem} />
            ) : undefined}
          />
        ) : (
          <div data-type="body-s" className="grid h-40 place-items-center text-on-surface-low">Loading…</div>
        )}
      </div>
    </WorkbenchLayout>
  )
}

function KnowledgeExtras({ item, pool, related, onOpenItem, annotations, onRemoveAnnotation,
  duplicates, duplicatesError, onRetryDuplicates, onMerged }: {
  item: KnowledgeItem
  pool: ExtractedContent[]
  related: KnowledgeItem[]
  onOpenItem: (id: string) => void
  annotations: KnowledgeAnnotation[]
  onRemoveAnnotation: (id: string) => void
  duplicates: KnowledgeDuplicate[]
  duplicatesError: unknown
  onRetryDuplicates: () => void
  onMerged: () => void
}) {
  const entities = item.entities ?? []
  const relations = item.relations ?? []
  const showDuplicates = duplicates.length > 0 || !!duplicatesError
  if (pool.length === 0 && entities.length === 0 && relations.length === 0 && related.length === 0
    && annotations.length === 0 && !showDuplicates && !item.content) {
    return <p data-type="body-s" className="text-on-surface-low">No extracted content, entities, or related items yet.</p>
  }
  return (
    <div className="flex flex-col gap-l">
      {
}
      <HighlightsSection annotations={annotations} onRemove={onRemoveAnnotation} />
      {
}
      {showDuplicates && (
        <Section label={`Possible duplicates${duplicates.length ? ` · ${duplicates.length}` : ''}`} icon={Copy}>
          <DuplicateList item={item} duplicates={duplicates} error={duplicatesError}
            onRetry={onRetryDuplicates} onOpenItem={onOpenItem} onMerged={onMerged} />
        </Section>
      )}
      {pool.length > 0 && (
        <Section label={`Extracted content · ${pool.length}`} icon={Layers}>
          <div className="flex flex-col gap-1.5">
            {pool.map((ec) => (
              <details key={ec.id} className="rounded-md bg-surface-container px-m py-1.5">
                <summary data-type="body-s" className="flex items-center gap-2 cursor-pointer text-on-surface-var">
                  <span data-type="caption" className="font-mono text-on-surface-low">{ec.node_type}</span>
                  {ec.backend && <span data-type="caption" className="text-on-surface-low">· {ec.backend}</span>}
                  <span data-type="caption" className="ml-auto text-on-surface-low">{(ec.text || '').length} chars</span>
                </summary>
                {ec.text && <div data-type="body-s" className="mt-1.5 max-h-72 overflow-y-auto text-on-surface-var leading-relaxed"><Markdown>{ec.text}</Markdown></div>}
              </details>
            ))}
          </div>
        </Section>
      )}
      <EntitiesSection entities={entities} />
      {relations.length > 0 && (
        <Section label={`Relations · ${relations.length}`}>
          <div className="flex flex-col gap-1">
            {relations.slice(0, 30).map((r) => (
              <div key={r.id} data-type="body-s" className="text-on-surface-var"><span className="text-on-surface">{r.source_name}</span> <span className="text-on-surface-low">{r.relation_type}</span> <span className="text-on-surface">{r.target_name}</span></div>
            ))}
            <MoreRow total={relations.length} shown={30} />
          </div>
        </Section>
      )}
      <RelatedSection related={related} onOpenItem={onOpenItem} />
    </div>
  )
}


export function HighlightsSection({ annotations, onRemove }: {
  annotations: KnowledgeAnnotation[]
  onRemove: (id: string) => void
}) {
  if (annotations.length === 0) return null
  return (
    <Section label={`Highlights · ${annotations.length}`} icon={Highlighter}>
      <AnnotationList annotations={annotations} onDelete={onRemove} />
    </Section>
  )
}

export function EntitiesSection({ entities }: { entities: NonNullable<KnowledgeItem['entities']> }) {
  if (entities.length === 0) return null
  return (
    <Section label={`Entities · ${entities.length}`} icon={Network}>
      <div className="flex flex-wrap gap-1.5">
        {entities.slice(0, 60).map((e) => (
          <span key={e.id} data-type="caption" className="inline-flex items-center gap-1 rounded-pill bg-surface-container px-2 h-6 text-on-surface-var" title={e.entity_type}>{e.name}{e.entity_type && <span className="text-on-surface-low">· {e.entity_type}</span>}</span>
        ))}
        <MoreRow total={entities.length} shown={60} className="px-1" />
      </div>
    </Section>
  )
}

export function RelatedSection({ related, onOpenItem }: {
  related: KnowledgeItem[]
  onOpenItem: (id: string) => void
}) {
  if (related.length === 0) return null
  return (
    <Section label={`Related · ${related.length}`} icon={Network}>
      <div className="flex flex-col gap-1">
        {related.slice(0, 15).map((r) => (
          <button key={r.id} type="button" onClick={() => onOpenItem(r.id)}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-surface-high">
            <span data-type="body-s" className="truncate text-on-surface">{r.title || '(untitled)'}</span>
            {
}
            {typeof r.score === 'number' ? (
              <span data-type="caption" className="ml-auto shrink-0 text-on-surface-low"
                title={[
                  `${Math.round(r.score * 100)}% similar`,
                  typeof r.chunk_index === 'number' && typeof r.neighbour_chunk_index === 'number'
                    ? `matched section ${r.chunk_index + 1} of this item against section ${r.neighbour_chunk_index + 1} of the other`
                    : '',
                  typeof r.shared_entities === 'number' ? `${r.shared_entities} shared entities` : '',
                ].filter(Boolean).join(' · ')}>
                {Math.round(r.score * 100)}%
              </span>
            ) : typeof r.shared_entities === 'number' ? (
              <span data-type="caption" className="ml-auto shrink-0 text-on-surface-low">{r.shared_entities} shared</span>
            ) : null}
          </button>
        ))}
        <MoreRow total={related.length} shown={15} />
      </div>
    </Section>
  )
}

export function hasReaderInsights(item: KnowledgeItem, related: KnowledgeItem[], annotations: KnowledgeAnnotation[]): boolean {
  return annotations.length > 0 || (item.entities ?? []).length > 0 || related.length > 0
}

export function ReaderInsights({ item, related, annotations, onRemoveAnnotation, onOpenItem }: {
  item: KnowledgeItem
  related: KnowledgeItem[]
  annotations: KnowledgeAnnotation[]
  onRemoveAnnotation: (id: string) => void
  onOpenItem: (id: string) => void
}) {
  return (
    <>
      <HighlightsSection annotations={annotations} onRemove={onRemoveAnnotation} />
      <EntitiesSection entities={item.entities ?? []} />
      <RelatedSection related={related} onOpenItem={onOpenItem} />
      <EgoGraphSection item={item} onOpenItem={onOpenItem} />
    </>
  )
}

function EgoGraphSection({ item, onOpenItem }: {
  item: KnowledgeItem
  onOpenItem: (id: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<KnowledgeGraphPayload | null>(null)
  const [err, setErr] = useState<unknown>(null)

  const entities = item.entities ?? []
  const focusId = (() => {
    if (!entities.length) return ''
    if (!data) return String(entities[0].id ?? '')
    const degree = new Map<string, number>()
    for (const e of data.edges) {
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1)
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1)
    }
    const ids = entities.map((e) => String(e.id ?? '')).filter(Boolean)
    return ids.slice().sort((a, b) => (degree.get(b) ?? 0) - (degree.get(a) ?? 0) || a.localeCompare(b))[0] ?? ''
  })()

  useEffect(() => {
    if (!open || data || err) return
    let alive = true
    api.knowledgeGraph()
      .then((d) => { if (alive) setData(d as KnowledgeGraphPayload) })
      .catch((e) => { if (alive) setErr(e) })
    return () => { alive = false }
  }, [open, data, err])

  if (!entities.length) return null
  return (
    <Section label="Neighbourhood" icon={Network}>
      <Button variant="ghost" size="sm" ariaExpanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? 'Hide the graph' : 'Show this in the graph'}
      </Button>
      {open && (
        err
          ? <LoadError what="knowledge graph" error={err} onRetry={() => setErr(null)} />
          : data
          ? <div className="mt-s"><KnowledgeEgoGraph data={data} focusId={focusId} onSelect={onOpenItem} /></div>
          : <div data-type="body-s" className="px-s py-m text-on-surface-low">Loading the graph…</div>
      )}
    </Section>
  )
}

function Section({ label, icon: Icon, children }: { label: string; icon?: typeof Network; children: React.ReactNode }) {
  return (
    <div>
      <div data-type="caption" className="mb-1.5 flex items-center gap-1.5 text-on-surface-low uppercase tracking-wide">{Icon && <Icon size={12} />}{label}</div>
      {children}
    </div>
  )
}
