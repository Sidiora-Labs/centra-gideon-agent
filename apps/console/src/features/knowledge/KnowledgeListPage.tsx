import { Fragment, useEffect, useMemo, useState } from 'react'
import { reportActionFailure, reportingWrite } from '../../app/shell/reportingWrite'
import { BookOpen, FileClock, Filter, Home, Plus, Search, Database, Sparkles, Network, Library, Trash2, Target, X, Pin, Star, Archive, Play, Pause, FileText, Loader2, CircleAlert, Boxes, WifiOff, Layers, Scale, Tag as TagIcon, Rss, ExternalLink, Gavel } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { fvs } from '../../shared/theme/fontWeight'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { Button } from '../../shared/ui/Button'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { DecisionJournal } from './DecisionJournal'
import { WindowedList } from '../../shared/ui/WindowedList'
import { Checkbox, FieldError } from '../../shared/ui/forms'
import { TagManager } from './TagManager'
import { ConflictPanel } from './ConflictPanel'
import { SidePanel } from '../../shared/ui/SidePanel'
import { ListControls } from '../../shared/ui/ListControls'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../shared/ui/HeaderActions'
import { ContextMenu, type ContextMenuItem } from '../../shared/ui/motion'
import { api, type KnowledgeIntent, type IntentOutcome, type KnowledgeItem, type KnowledgeCollection, type KnowledgeBulkOp } from '../../shared/data/api'
import { resolveType, relTime, fmtBytes, typeLabel, isArtifactItem } from './knowledgeMeta'
import { listKnowledge, knowledgeStats, getKnowledge } from './knowledgeStore'
import { KnowledgeDetail, OutcomeFieldValue } from './KnowledgeDetail'
import { KnowledgeGraph } from './KnowledgeGraph'
import { LibraryHome } from './LibraryHome'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { rowSubject } from '../../shared/data/rowSubject'
import { confirm, confirmDelete, promptInput } from '../../shared/ui/dialog'
import { PageTitle } from '../../shared/ui/PageTitle'
import { notify } from '../../app/shell/appSdk'
import { BUSY_REASON } from '../../shared/ui/unavailable'
import { readingTimeLabel } from './readingTime'
import { AuiKnowledgePanel } from './auiKnowledgePanel'

type View = 'home' | 'library' | 'graph' | 'intents' | 'tags' | 'conflicts' | 'decisions'

function StatChip({ icon: Icon, label, value }: { icon: typeof Database; label: string; value: number | string }) {
  return (
    <div className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2">
      <Icon size={15} className="text-primary shrink-0" />
      <span data-type="title-m" className="text-on-surface tabular-nums" style={fvs(500)}>{value}</span>
      <span data-type="caption" className="text-on-surface-low">{label}</span>
    </div>
  )
}

function EmbeddingChip({ stats, busy, onBackfill }: { stats: import('../../shared/data/api').KnowledgeStats; busy: boolean; onBackfill: (rebuild?: boolean) => void }) {
  const e = stats.embeddings
  if (!e?.enabled) {
    return (
      <div className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2 text-on-surface-low" title="No embedding model active — search is keyword + entity-graph only. Set one in Settings › AI & Models.">
        <Boxes size={15} className="shrink-0" />
        <span data-type="caption" >semantic search off</span>
      </div>
    )
  }
  const embedded = e.embedded_items ?? 0
  const stale = e.stale_items ?? 0
  const behind = Math.max(0, stats.items - embedded)
  if (stale > 0) {
    return (
      <button type="button" onClick={() => onBackfill(true)} disabled={busy}
        title={`${stale} item${stale === 1 ? '' : 's'} embedded with a previous model — click to re-embed all with ${e.model} (semantic search ignores stale vectors until then)`}
        className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2 transition-colors hover:bg-surface-high disabled:opacity-60">
        <Boxes size={15} className={`shrink-0 ${busy ? 'animate-pulse text-primary' : 'text-warning'}`} />
        <span data-type="title-m" className="text-on-surface tabular-nums" style={fvs(500)}>{stale}</span>
        <span data-type="caption" className="text-on-surface-low">{busy ? 'embedding…' : 'stale — re-embed'}</span>
      </button>
    )
  }
  if (behind > 0) {
    return (
      <button type="button" onClick={() => onBackfill(false)} disabled={busy}
        title={`${behind} item${behind === 1 ? '' : 's'} not yet embedded — click to backfill (model: ${e.model})`}
        className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2 transition-colors hover:bg-surface-high disabled:opacity-60">
        <Boxes size={15} className={`shrink-0 ${busy ? 'animate-pulse text-primary' : 'text-warning'}`} />
        <span data-type="title-m" className="text-on-surface tabular-nums" style={fvs(500)}>{embedded}/{stats.items}</span>
        <span data-type="caption" className="text-on-surface-low">{busy ? 'embedding…' : 'embed rest'}</span>
      </button>
    )
  }
  return (
    <div className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2" title={`All items embedded for semantic search (model: ${e.model})`}>
      <Boxes size={15} className="text-primary shrink-0" />
      <span data-type="title-m" className="text-on-surface tabular-nums" style={fvs(500)}>{embedded}</span>
      <span data-type="caption" className="text-on-surface-low">embedded</span>
    </div>
  )
}

export function KnowledgeListPage({ onCreate, onOpenItem, onOpenReader, onOpenSources, onOpenReports, onOpenChat, query, setQuery }: { onCreate: () => void; onOpenItem: (id: string) => void; onOpenReader?: (id: string) => void; onOpenSources: () => void; onOpenReports: () => void; onOpenChat: () => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const [viewRaw, setView] = useQueryParam(query, setQuery, 'view', 'library', { replace: true })
  const view = viewRaw as View
  const [submitted, setSubmitted] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [q, setQ] = useState(submitted)
  useEffect(() => {
    if (q === submitted) return
    const t = setTimeout(() => setSubmitted(q), 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q])
  const [typeFilter, setTypeFilter] = useQueryParam(query, setQuery, 'type', '')
  const [providerFilter, setProviderFilter] = useQueryParam(query, setQuery, 'provider', '')
  const [tagFilter, setTagFilter] = useQueryParam(query, setQuery, 'tag', '', { replace: true })
  const [entityTok, setEntityTok] = useQueryParam(query, setQuery, 'entity', '')
  const selectedEntity = entityTok || null
  const setSelectedEntity = (name: string | null) => setEntityTok(name || '')
  const [itemTok, setItemTok] = useQueryParam(query, setQuery, 'item', '')
  const peekId = itemTok || null
  const [intentTok, setIntentTok] = useQueryParam(query, setQuery, 'intent', '')
  const [resolvedIntent, setResolvedIntent] = useState<KnowledgeIntent | null>(null)
  const selectedIntent: KnowledgeIntent | null = intentTok === '__new__'
    ? blankIntent()
    : (intentTok ? resolvedIntent : null)
  const setSelectedIntent = (it: KnowledgeIntent | null) => {
    setResolvedIntent(it && it.id ? it : null)
    setIntentTok(it ? (it.id || '__new__') : '')
  }
  const [intentsReloadKey, setIntentsReloadKey] = useState(0)
  const refreshIntents = () => setIntentsReloadKey((k) => k + 1)

  const [peekItem, setPeekItem] = useState<KnowledgeItem | null>(null)
  useEffect(() => {
    if (!peekId) { setPeekItem(null); return }
    let alive = true
    getKnowledge(peekId).then((d) => { if (alive) setPeekItem(d ?? null) }).catch(() => alive && setPeekItem(null))
    return () => { alive = false }
  }, [peekId])

  const [showArchived, setShowArchived] = useState(false)
  const [curationFilter, setCurationFilter] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkNote, setBulkNote] = useState('')
  const selecting = selected.size > 0
  const clearSelection = () => setSelected(new Set())
  const toggleSelected = (id: string) => setSelected((prev) => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })
  const [collectionTok, setCollectionTok] = useQueryParam(query, setQuery, 'collection', '', { replace: true })
  const { data: collectionsData, refresh: refreshCollections } =
    useQuery<KnowledgeCollection[]>('knowledge:collections', () => api.knowledgeCollections().catch(() => []))
  const collections = collectionsData ?? []
  const activeCollection = collectionTok ? collections.find((c) => c.id === collectionTok) ?? null : null

  const itemsKey = collectionTok
    ? `knowledge:collection-items:${collectionTok}`
    : `knowledge:items:${submitted}:${showArchived ? 'arch' : ''}`
  const { data: itemsData, error: itemsErr, loading: itemsLoading, stale: itemsStale, refresh: refreshItems } =
    useQuery(itemsKey, () => (collectionTok
      ? api.knowledgeCollectionItems(collectionTok, 200).then((r) => r.items)
      : listKnowledge({ q: submitted || undefined, includeArchived: showArchived })))
  const { data: statsData, refresh: refreshStats } = useQuery('knowledge:stats', () => knowledgeStats())
  const items = itemsData ?? null
  const stats = statsData ?? null
  const load = () => { refreshItems(); refreshStats(); refreshCollections() }

  const runBulk = async (op: KnowledgeBulkOp, args: Record<string, unknown> = {}, verb = 'Updated') => {
    if (!selected.size || bulkBusy) return
    setBulkBusy(true); setBulkNote('')
    try {
      const res = await api.knowledgeBulk(op, [...selected], args)
      const parts = [`${verb} ${res.changed.length}`]
      if (res.unchanged.length) parts.push(`${res.unchanged.length} already set`)
      if (res.missing.length) parts.push(`${res.missing.length} not found`)
      setBulkNote(parts.join(' · '))
      clearSelection()
      load()
    } catch (e) {
      const msg = String((e as Error)?.message || e)
      setBulkNote(msg.includes('smart_collection_immutable')
        ? "A smart shelf fills itself from its query — items can't be added by hand."
        : 'Bulk action failed — nothing was changed.')
    } finally {
      setBulkBusy(false)
    }
  }

  async function createCollection() {
    const name = await promptInput({ title: 'New shelf', label: 'Shelf name', placeholder: 'e.g. Reading list', confirmLabel: 'Create' })
    if (!name) return
    const q = await promptInput({
      title: 'Smart shelf?',
      label: 'Search that fills it (leave empty for a manual shelf)',
      placeholder: 'e.g. rust ownership',
      confirmLabel: 'Create shelf',
    })
    const res = await api.createKnowledgeCollection(q ? { name, kind: 'smart', query: q } : { name })
      .catch(reportActionFailure(`create the shelf “${name}”`))
    if (!res) return
    invalidateKeys('knowledge:collections')
    refreshCollections()
    setCollectionTok(res.collection.id)
  }

  async function shelveItem(c: KnowledgeCollection, it: KnowledgeItem) {
    const ok = await reportingWrite(`add "${it.title || 'this item'}" to "${c.name}"`,
      () => api.addToKnowledgeCollection(c.id, [it.id]))
    if (!ok) return
    invalidateKeys('knowledge:collections')
    refreshCollections()
  }

  async function unshelveItem(c: KnowledgeCollection, it: KnowledgeItem) {
    const ok = await reportingWrite(`remove "${it.title || 'this item'}" from "${c.name}"`,
      () => api.removeFromKnowledgeCollection(c.id, it.id))
    if (!ok) return
    invalidateKeys(itemsKey)
    invalidateKeys('knowledge:collections')
    refreshItems(); refreshCollections()
  }

  async function cycleReadState(it: KnowledgeItem) {
    const next = it.read_state === 'reading' ? 'read' : it.read_state === 'read' ? 'unread' : 'reading'
    const ok = await reportingWrite(`mark "${it.title || 'this item'}" as ${next}`,
      () => api.setKnowledgeReadState(it.id, next))
    if (!ok) return
    invalidateKeys(itemsKey)
    refreshItems()
  }

  async function toggleFavorite(it: KnowledgeItem) {
    const ok = await reportingWrite(
      `${it.favorited ? 'unfavourite' : 'favourite'} "${it.title || 'this item'}"`,
      () => api.setKnowledgeFavorited(it.id, !it.favorited))
    if (!ok) return
    invalidateKeys(itemsKey)
    refreshItems()
  }

  async function renameCollection(c: KnowledgeCollection) {
    const name = await promptInput({ title: 'Rename shelf', label: 'Shelf name', initial: c.name, confirmLabel: 'Rename' })
    if (!name || name === c.name) return
    const ok = await reportingWrite(`rename "${c.name}"`,
      () => api.updateKnowledgeCollection(c.id, { name }))
    if (!ok) return
    invalidateKeys('knowledge:collections')
    refreshCollections()
  }

  async function removeCollection(c: KnowledgeCollection) {
    const ok = await confirm({
      title: `Delete "${c.name}"?`,
      body: 'The shelf goes away. The items on it stay in your library.',
      confirmLabel: 'Delete shelf',
      danger: true,
    })
    if (!ok) return
    try { await api.deleteKnowledgeCollection(c.id) }
    catch (e) {
      notify(`Couldn't delete the shelf "${c.name}": ${String((e as Error)?.message || e)}`, 'error')
      invalidateKeys('knowledge:collections'); refreshCollections()
      return
    }
    invalidateKeys('knowledge:collections')
    refreshCollections()
    setCollectionTok('')
  }

  const [regenning, setRegenning] = useState(false)
  const regenerate = async () => {
    setRegenning(true)
    try { await api.regenerateKnowledgeIntelligence('missing') } catch {   }
    finally { setRegenning(false); load() }
  }

  const [embedding, setEmbedding] = useState(false)
  const backfillEmbeddings = async (rebuild = false) => {
    setEmbedding(true)
    try { await api.generateKnowledgeEmbeddings(rebuild) } catch {   }
    finally { setEmbedding(false); refreshStats() }
  }

  const anyProcessing = useMemo(
    () => (items ?? []).some((it) => it.processing_status === 'queued' || it.processing_status === 'processing'),
    [items],
  )
  useEffect(() => {
    if (!anyProcessing) return
    const t = setInterval(() => { refreshItems(); refreshStats() }, 3000)
    return () => clearInterval(t)
  }, [anyProcessing, refreshItems, refreshStats])

  const typesPresent = useMemo(() => {
    const set = new Set<string>()
    for (const it of items ?? []) set.add(resolveType(it).key)
    return [...set]
  }, [items])
  const providersPresent = useMemo(() => {
    const set = new Set<string>()
    for (const it of items ?? []) set.add(it.provider || 'native')
    return [...set]
  }, [items])
  const shown = useMemo(
    () => (items ?? []).filter((it) =>
      (!typeFilter || resolveType(it).key === typeFilter) &&
      (!providerFilter || (it.provider || 'native') === providerFilter) &&
      (!tagFilter || (it.tags ?? []).includes(tagFilter)) &&
      (!curationFilter || (curationFilter === 'favorites'
        ? !!it.favorited
        : (it.read_state || 'unread') === curationFilter))),
    [items, typeFilter, providerFilter, tagFilter, curationFilter],
  )
  const curationCounts = useMemo(() => {
    const base = items ?? []
    return {
      unread: base.filter((i) => (i.read_state || 'unread') === 'unread').length,
      reading: base.filter((i) => i.read_state === 'reading').length,
      read: base.filter((i) => i.read_state === 'read').length,
      favorites: base.filter((i) => !!i.favorited).length,
    }
  }, [items])
  const empty = stats && stats.items === 0
  const knowledgeSearch = view === 'library' ? submitted.trim() : ''

  return (
    <WorkbenchLayout
      scroll={view !== 'graph'}
      controls={view === 'library'
        ? <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search knowledge', label: 'Search knowledge' }}
            results={{ count: (items ?? []).length, noun: 'items', active: !!submitted }}
            stale={itemsStale} />
        : undefined}
      topBar={
        <TopBar
          keepCornerPadding
          left={<PageTitle>Knowledge</PageTitle>}
          right={
            <HeaderActions>
              <HeaderSegmented ariaLabel="Knowledge view" value={view} onChange={(v) => setView(v as View)}
                options={[{ key: 'home', label: 'Home', icon: Home }, { key: 'library', label: 'Library', icon: Library }, { key: 'graph', label: 'Graph', icon: Network }, { key: 'intents', label: 'Intents', icon: Target }, { key: 'tags', label: 'Tags', icon: TagIcon }, { key: 'conflicts', label: 'Conflicts', icon: Scale }, { key: 'decisions', label: 'Decisions', icon: Gavel }]} />
              {view === 'library' && (items?.length ?? 0) > 0 && (
                <HeaderControl icon={Sparkles} label="Regenerate intelligence" priority="low"
                  hint="Re-derive insights for items missing them"
                  disabled={regenning} onClick={regenning ? undefined : regenerate} />
              )}
              {
}
              <HeaderControl icon={Rss} label="Sources" priority="low"
                hint="Pages, feeds and folders that fill your library on their own"
                onClick={onOpenSources} />
                {
}
                <HeaderControl icon={FileClock} label="Reports" priority="low"
                  hint="Watch part of your library on a schedule and write up what is new"
                  onClick={onOpenReports} />
              {view === 'intents'
                ? <HeaderControl icon={Plus} label="New intent" variant="primary" priority="primary"
                    onClick={() => setSelectedIntent(blankIntent())} />
                : <HeaderControl icon={Plus} label="Add knowledge" variant="primary" priority="primary"
                    onClick={onCreate} />}
            </HeaderActions>
          }
        />
      }
      panel={
        view === 'library' && peekId ? (
          <SidePanel key={peekId} fillHeight storeKey="knowledge-panel-w" urlKey={{ key: 'item', setQuery }}
            icon={peekItem ? (() => { const tm = resolveType(peekItem); return <tm.icon size={18} style={{ color: tm.tone }} /> })() : <FileText size={18} className="text-primary" />}
            title={peekItem?.title || peekItem?.url_title || 'Knowledge item'}
            onExpand={() => onOpenItem(peekId)}
            onClose={() => setItemTok('')}>
            {peekItem ? (
              <div className="h-full min-h-[60vh]">
                <KnowledgeDetail
                  item={peekItem}
                  onChanged={() => { getKnowledge(peekId).then((d) => setPeekItem(d ?? null)).catch(() => {}); load() }}
                  onDeleted={() => { setItemTok(''); load() }}
                  onTagClick={(t) => { setItemTok(''); setTagFilter(t) }}
                />
              </div>
            ) : (
              <ListSkeleton rows={6} />
            )}
          </SidePanel>
        ) : view === 'graph' && selectedEntity ? (
          <SidePanel key={selectedEntity} fillHeight storeKey="knowledge-panel-w" urlKey={{ key: 'entity', setQuery }} icon={<Sparkles size={18} className="text-primary" />} title={selectedEntity} onClose={() => setSelectedEntity(null)}>
            <EntityDetail name={selectedEntity} onOpenItem={(id) => onOpenItem(id)} onSelectEntity={setSelectedEntity} />
          </SidePanel>
        ) : view === 'intents' && selectedIntent ? (
          <SidePanel key={selectedIntent.id || '__new__'} fillHeight storeKey="knowledge-panel-w" urlKey={{ key: 'intent', setQuery }} icon={<Target size={18} className="text-primary" />} title={selectedIntent.id ? (selectedIntent.goal || selectedIntent.id) : 'New intent'} onClose={() => setSelectedIntent(null)}>
            {selectedIntent.id
              ? <IntentDetail intent={selectedIntent} onChanged={refreshIntents} onClose={() => setSelectedIntent(null)} onOpenItem={(id) => onOpenItem(id)} />
              : <IntentEditor intent={selectedIntent} onClose={() => setSelectedIntent(null)} onSaved={() => { setSelectedIntent(null); refreshIntents() }} />}
          </SidePanel>
        ) : null
      }
    >
      {stats && (
        <div className="mx-auto w-full px-l pt-l" style={{ maxWidth: 'var(--content-width)' }}>
          <div className="flex flex-wrap items-center gap-s">
            <StatChip icon={Database} label="items" value={stats.items} />
            <StatChip icon={Sparkles} label="entities" value={stats.entities} />
            <StatChip icon={Network} label="relations" value={stats.relations} />
            <EmbeddingChip stats={stats} busy={embedding} onBackfill={backfillEmbeddings} />
          </div>
        </div>
      )}

      {knowledgeSearch && <div className="mx-auto w-full px-l pt-l" style={{ maxWidth: 'var(--content-width)' }}>
        <AuiKnowledgePanel query={knowledgeSearch} onOpen={onOpenItem} />
      </div>}

      {
}
      {view === 'graph' && !empty && (
        <div className="flex-1 min-h-0 px-l pb-l pt-m">
          {
}
          <KnowledgeGraph selectedId={selectedEntity} onSelect={setSelectedEntity}
            onRegenerate={regenerate} regenerating={regenning} />
        </div>
      )}

      {
}
      {(view !== 'graph' || empty) && (
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {view === 'home' && !empty ? (
          <LibraryHome
            onOpenItem={onOpenItem}
            onOpenReader={onOpenReader ?? onOpenItem}
            onOpenCollection={(id) => { setCollectionTok(id); setView('library') }}
            onShowCuration={(f) => { setCurationFilter(f); setView('library') }} />
        ) : view === 'decisions' ? (
          <DecisionJournal onOpenItem={onOpenItem} onOpenChat={onOpenChat} />
        ) : itemsData === undefined && itemsErr ? (
              <LoadError what={collectionTok ? 'shelf items' : 'knowledge items'} error={itemsErr} onRetry={load} />
            ) : items === null ? (itemsLoading ? <ListSkeleton what={collectionTok ? 'shelf items' : 'knowledge items'} /> : null) : empty ? (
              <EmptyState icon={BookOpen} title="Knowledge base is empty" hint="Add notes, code gists, bookmarks, documents, images, audio, and video. Content is extracted, entities surfaced, and everything indexed for agents to retrieve." action={{ label: 'Add knowledge', onClick: onCreate, icon: Plus }} />
            ) : view === 'conflicts' ? (
              <ConflictPanel />
            ) : view === 'tags' ? (
              <TagManager onChanged={load} />
            ) : view === 'intents' ? (
              <IntentsView selectedId={selectedIntent?.id ?? null} onSelect={setSelectedIntent} reloadKey={intentsReloadKey} />
            ) : (
              <>
                <div className="mb-l flex flex-wrap gap-1.5">
                  {typesPresent.length > 1 && <FilterChip active={typeFilter === ''} onClick={() => setTypeFilter('')}>All types</FilterChip>}
                  {typesPresent.length > 1 && typesPresent.map((t) => { const tm = resolveType({ type: t as never }); return <FilterChip key={t} active={typeFilter === t} onClick={() => setTypeFilter(t)} tone={tm.tone}><tm.icon size={12} /> {tm.label}</FilterChip> })}
                  {providersPresent.length > 1 && <FilterChip active={providerFilter === ''} onClick={() => setProviderFilter('')}><Database size={12} /> All providers</FilterChip>}
                  {providersPresent.length > 1 && providersPresent.map((p) => <FilterChip key={p} active={providerFilter === p} onClick={() => setProviderFilter(p)}>{p === 'native' ? 'Gideon' : p}</FilterChip>)}
                  {
}
                  {(items?.length ?? 0) > 1 && curationCounts.reading > 0 && (
                    <FilterChip active={curationFilter === 'reading'} onClick={() => setCurationFilter(curationFilter === 'reading' ? '' : 'reading')}>
                      <BookOpen size={12} /> Reading {curationCounts.reading}
                    </FilterChip>
                  )}
                  {(items?.length ?? 0) > 1 && curationCounts.unread > 0 && curationCounts.unread !== (items?.length ?? 0) && (
                    <FilterChip active={curationFilter === 'unread'} onClick={() => setCurationFilter(curationFilter === 'unread' ? '' : 'unread')}>
                      Unread {curationCounts.unread}
                    </FilterChip>
                  )}
                  {(items?.length ?? 0) > 1 && curationCounts.read > 0 && (
                    <FilterChip active={curationFilter === 'read'} onClick={() => setCurationFilter(curationFilter === 'read' ? '' : 'read')}>
                      Read {curationCounts.read}
                    </FilterChip>
                  )}
                  {curationCounts.favorites > 0 && (
                    <FilterChip active={curationFilter === 'favorites'} onClick={() => setCurationFilter(curationFilter === 'favorites' ? '' : 'favorites')} tone="var(--color-primary)">
                      <Star size={12} /> Favorites {curationCounts.favorites}
                    </FilterChip>
                  )}
                  <FilterChip active={showArchived} onClick={() => setShowArchived((v) => !v)}><Archive size={12} /> {showArchived ? 'Showing archived' : 'Show archived'}</FilterChip>
                  {tagFilter && <FilterChip active onClick={() => setTagFilter('')}># {tagFilter} <X size={11} /></FilterChip>}
                </div>
                {
}
                <div className="mb-m flex flex-wrap items-center gap-1.5">
                  <FilterChip active={!collectionTok} onClick={() => setCollectionTok('')}>
                    <Library size={12} /> All items
                  </FilterChip>
                  {collections.map((c) => (
                    <FilterChip key={c.id} active={collectionTok === c.id} onClick={() => setCollectionTok(c.id)}>
                      {c.kind === 'smart' ? <Sparkles size={12} /> : <Layers size={12} />}
                      <span className="max-w-48 truncate" title={c.name}>{c.name}</span>
                      {c.kind === 'manual' && typeof c.item_count === 'number' ? ` ${c.item_count}` : ''}
                    </FilterChip>
                  ))}
                  {
}
                  <FilterChip active={false} onClick={createCollection}>
                    <Plus size={12} /> New shelf
                  </FilterChip>
                </div>
                {activeCollection && (
                  <div className="mb-m flex flex-wrap items-center gap-2">
                    <span data-type="label-l" className="text-on-surface">{activeCollection.name}</span>
                    <span data-type="caption" className="text-on-surface-low">
                      {activeCollection.kind === 'smart'
                        ? `Smart shelf — everything matching "${activeCollection.query}", kept current automatically.`
                        : 'Manual shelf — the items you put here.'}
                    </span>
                    <Button variant="ghost" size="xs" onClick={() => renameCollection(activeCollection)}>Rename</Button>
                    <Button variant="ghost" size="xs" onClick={() => removeCollection(activeCollection)}>Delete shelf</Button>
                  </div>
                )}
                {selecting && (
                  <div className="mb-m flex flex-wrap items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
                    <span data-type="label-s" className="text-on-surface" style={fvs(500)}>
                      {selected.size} selected
                    </span>
                    <Button variant="tonal" size="xs" disabled={bulkBusy} disabledReason={BUSY_REASON}
                      onClick={() => runBulk('read_state', { state: 'read' }, 'Marked read')}>
                      Mark read
                    </Button>
                    <Button variant="tonal" size="xs" disabled={bulkBusy} disabledReason={BUSY_REASON}
                      onClick={() => runBulk('read_state', { state: 'unread' }, 'Marked unread')}>
                      Mark unread
                    </Button>
                    <Button variant="tonal" size="xs" disabled={bulkBusy} disabledReason={BUSY_REASON}
                      onClick={() => runBulk('favorite', { value: true }, 'Favorited')}>
                      Favorite
                    </Button>
                    {
}
                    {collections.filter((c) => c.kind === 'manual').map((c) => (
                      <Button key={c.id} variant="tonal" size="xs" disabled={bulkBusy} disabledReason={BUSY_REASON}
                        onClick={() => runBulk('collect', { collection_id: c.id }, `Added to ${c.name}:`)}>
                        Add to {c.name}
                      </Button>
                    ))}
                    <Button variant="tonal" size="xs" disabled={bulkBusy} disabledReason={BUSY_REASON}
                      onClick={() => runBulk(showArchived ? 'restore' : 'archive', {}, showArchived ? 'Restored' : 'Archived')}>
                      {showArchived ? 'Restore' : 'Archive'}
                    </Button>
                    <Button variant="secondary" size="xs" onClick={clearSelection} className="ml-auto">
                      Clear
                    </Button>
                  </div>
                )}
                {
}
                {bulkNote && !selecting && (
                  <div role="status" data-type="body-s" className="mb-m text-on-surface-var">{bulkNote}</div>
                )}
                {(shown?.length ?? 0) === 0 ? (
                  submitted ? (
                    <EmptyState icon={Search} title={`No items match “${submitted}”`}
                      hint="Try different words, or clear the search to browse the library."
                      action={{ label: 'Clear search', onClick: () => { setQ(''); setSubmitted('') } }} />
                  ) : typeFilter || providerFilter || tagFilter || curationFilter ? (
                    <EmptyState icon={Filter} title="No items in this view"
                      hint="Nothing matches the active filters."
                      action={{ label: 'View all items', onClick: () => { setTypeFilter(''); setProviderFilter(''); setTagFilter(''); setCurationFilter('') } }} />
                  ) : (
                    <EmptyState icon={Search} title="Nothing here"
                      hint={collectionTok ? 'This shelf has no items yet.' : 'No items to show.'} />
                  )
                ) : (
                  <WindowedList
                    items={shown!}
                    rowKey={(it) => it.id}
                    rowHeights="variable"
                    estimateRowHeight={76}
                    gap={8}
                    noun="items"
                    findHint="use the Search knowledge field above, which searches contents as well as titles."
                    anchorKey={peekId ?? undefined}
                    className="flex flex-col gap-s"
                  >
                    {(it, i, listCtx) => {
                      const tm = resolveType(it)
                      const readTime = readingTimeLabel(it)
                      const manualShelves = collections.filter((c) => c.kind === 'manual')
                      const menuItems: ContextMenuItem[] = [
                        { icon: <FileText size={15} />, label: 'Peek', onSelect: () => setItemTok(it.id) },
                        { icon: <Library size={15} />, label: 'Open full page', onSelect: () => onOpenItem(it.id) },
                        ...(readTime ? [{ icon: <BookOpen size={15} />, label: `Read · ${readTime}`, onSelect: () => (onOpenReader ?? onOpenItem)(it.id) }] : []),
                        {
                          icon: <BookOpen size={15} />,
                          label: it.read_state === 'reading' ? 'Mark as read'
                            : it.read_state === 'read' ? 'Mark as unread' : 'Mark as reading',
                          onSelect: () => cycleReadState(it),
                        },
                        {
                          icon: <Star size={15} />,
                          label: it.favorited ? 'Remove favorite' : 'Favorite',
                          onSelect: () => toggleFavorite(it),
                        },
                        ...manualShelves
                          .filter((c) => c.id !== collectionTok)
                          .map((c) => ({
                            icon: <Layers size={15} />,
                            label: `Add to ${c.name}`,
                            onSelect: () => shelveItem(c, it),
                          })),
                        ...(activeCollection && activeCollection.kind === 'manual'
                          ? [{
                            icon: <X size={15} />,
                            label: `Remove from ${activeCollection.name}`,
                            onSelect: () => unshelveItem(activeCollection, it),
                          }]
                          : []),
                      ]
                      return (
                        <ContextMenu key={it.id} items={menuItems}>
                        { }
                        <ListRow index={listCtx.windowed ? 0 : i} accent={tm.tone} onClick={() => setItemTok(peekId === it.id ? '' : it.id)} label={it.title || it.url_title || '(untitled)'}>
                          {
}
                          <span onClick={(e) => e.stopPropagation()}
                            className={`shrink-0 transition-opacity ${selecting ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'}`}>
                            <Checkbox checked={selected.has(it.id)} onChange={() => toggleSelected(it.id)}
                              ariaLabel={`Select ${it.title || it.url_title || 'item'}`} />
                          </span>
                          {tm.key === 'image' && it.file_path
                            ? <img src={api.knowledgeItemThumbnailUrl(it.id)} alt="" className="shrink-0 size-10 rounded-lg object-cover bg-surface-container" onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }} />
                            : <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${tm.tone} 16%, transparent)` }}><tm.icon size={19} style={{ color: tm.tone }} /></span>}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-s">
                              {it.is_pinned && <Pin size={12} className="shrink-0 text-primary" style={{ fill: 'currentColor' }} />}
                              {
}
                              {it.favorited && <Star size={12} className="shrink-0 text-primary" style={{ fill: 'currentColor' }} aria-label="Favorited" />}
                              {
}
                              {it.read_state === 'reading' && (
                                <span data-type="caption" className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-primary-emphasis" title="You're partway through this">
                                  <BookOpen size={10} /> reading
                                </span>
                              )}
                              <span data-type="title-m" className={`truncate ${it.read_state === 'read' ? 'text-on-surface-var' : 'text-on-surface'}`} style={fvs(it.read_state === 'read' ? 400 : 500)}>{it.title || it.url_title || '(untitled)'}</span>
                              {(it.processing_status === 'queued' || it.processing_status === 'processing') && (
                                <span data-type="caption" className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-primary-emphasis"><Loader2 size={10} className="animate-spin" /> Enriching</span>
                              )}
                              {it.processing_status === 'failed' && (
                                <span data-type="caption" className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-danger" title={it.processing_error || 'Enrichment failed'}><CircleAlert size={10} /> Failed</span>
                              )}
                              {
}
                              {it.processing_status === 'unreachable' && (
                                <span data-type="caption" className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5" style={{ color: 'var(--color-warning)' }} title={`${it.processing_error || "Couldn't reach the site"} — open to retry`}><WifiOff size={10} /> Unreachable</span>
                              )}
                              {
}
                              {it.processing_status === 'partial' && !(it.processing_error || '').startsWith('Skipped (optional steps unavailable):') && (
                                <span data-type="caption" className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5" style={{ color: 'var(--color-warning)' }} title={`${it.processing_error || 'Enrichment incomplete'} — open to regenerate`}><CircleAlert size={10} /> Incomplete</span>
                              )}
                              {it.is_archived && <span data-type="caption" className="shrink-0 rounded-pill bg-surface-high px-1.5 text-on-surface-low">Archived</span>}
                              {it._match_type && <span data-type="caption" className="shrink-0 rounded-pill bg-surface-high px-1.5 text-on-surface-low">{it._match_type}</span>}
                            </div>
                            <div data-type="body-s" className="mt-0.5 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low">
                              <span style={{ color: tm.tone }}>{typeLabel(it)}</span>
                              {
}
                              {it.provider && it.provider !== 'native' && !isArtifactItem(it) && <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 text-on-surface-var">{it.provider}</span>}
                              {
}
                              {isArtifactItem(it) && !!it.guid && (
                                <a href={`#/artifacts/${encodeURIComponent(it.guid)}`} onClick={(e) => e.stopPropagation()}
                                  data-type="caption" className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 text-primary-emphasis transition-colors hover:bg-surface-container">
                                  <ExternalLink size={10} aria-hidden /> Open artifact
                                </a>
                              )}
                              {it.file_size != null && it.file_size > 0 && <span>· {fmtBytes(it.file_size)}</span>}
                              {readTime && (
                                <button type="button" onClick={(e) => { e.stopPropagation(); (onOpenReader ?? onOpenItem)(it.id) }}
                                  aria-label={`Read ${it.title || it.url_title || 'untitled item'}, ${readTime}`}
                                  className="inline-flex items-center gap-1 text-primary-emphasis transition-colors hover:text-primary">
                                  <BookOpen size={11} aria-hidden /> {readTime}
                                </button>
                              )}
                              {
}
                              {(it.summary || it.content) && <span className="truncate">{it.summary || it.content}</span>}
                            </div>
                          </div>
                          {(it.tags?.length ?? 0) > 0 && <div className="hidden md:flex shrink-0 gap-1">{it.tags!.slice(0, 2).map((t) => <button key={t} type="button" onClick={(e) => { e.stopPropagation(); setTagFilter(t) }} title={`Filter by "${t}"`} data-type="caption" className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var transition-colors hover:bg-surface-container hover:text-primary">{t}</button>)}</div>}
                          {it.updated_at && <span data-type="caption" className="hidden sm:block shrink-0 text-on-surface-low">{relTime(it.updated_at)}</span>}
                        </ListRow>
                        </ContextMenu>
                      )
                    }}
                  </WindowedList>
                )}
          </>
        )}
      </div>
      )}
    </WorkbenchLayout>
  )
}

async function confirmIntentDelete(goal: string, gathered: number): Promise<boolean> {
  return confirmDelete('intent', rowSubject([goal], 40), {
    body: gathered > 0
      ? `Everything it gathered goes with it — ${gathered} ${gathered === 1 ? 'match' : 'matches'}, kept by value, so re-adding the intent will not bring them back.`
      : 'It has gathered nothing yet, so only the intent itself goes.',
  })
}

function blankIntent(): KnowledgeIntent {
  return { id: '', goal: '', enabled: true, enabled_for: [], propose_skill: false }
}

function IntentsView({ selectedId, onSelect, reloadKey }: {
  selectedId: string | null
  onSelect: (intent: KnowledgeIntent | null) => void
  reloadKey: number
}) {
  const [intents, setIntents] = useState<KnowledgeIntent[] | null>(null)
  const [intentsErr, setIntentsErr] = useState<unknown>(null)
  const load = () => api.knowledgeIntents()
    .then((r) => { setIntentsErr(null); setIntents(r.intents) })
    .catch((e) => { setIntentsErr(e); setIntents([]) })
  useEffect(() => { load() }, [reloadKey])
  useEffect(() => {
    if (!selectedId || selectedId === '__new__' || !intents) return
    const match = intents.find((it) => it.id === selectedId)
    if (match) onSelect(match)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, intents])
  if (intentsErr) return <LoadError what="intents" error={intentsErr} onRetry={load} />
  if (intents === null) return <ListSkeleton rows={3} what="intents" />
  return (
    <div className="flex flex-col gap-s">
      <p data-type="body-s" className="text-on-surface-low">Tell Gideon what to watch for in plain language. As you save items, it gathers what matches — with the specifics extracted as structured fields. Click an intent to see everything it found, or add one with “New intent”.</p>
      {intents.length === 0 && (
        <EmptyState icon={Target} title="No intents yet"
          hint='e.g. "anything that could improve my homelab", "ideas that help me learn agentic engineering", or "hints on how I should invest".'
          action={{ label: 'New intent', onClick: () => onSelect(blankIntent()), icon: Plus }} />
      )}
      {intents.map((it) => (
        <ListRow key={it.id} index={0} accent={it.id === selectedId ? 'var(--color-primary)' : undefined} onClick={() => onSelect(it)} label={it.goal || it.id}>
          <Target size={15} className="shrink-0 text-primary/80" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              {
}
              <span data-type="body-m" className="truncate text-on-surface" title={it.goal || it.id}>{it.goal || it.id}</span>
              {!it.enabled && <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 text-on-surface-low">off</span>}
              {it.propose_skill && <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 text-primary-emphasis">proposes skill</span>}
            </div>
            <div data-type="caption" className="truncate text-on-surface-low">
              {(it.outcome_count ?? 0) > 0 ? `${it.outcome_count} gathered` : it.enabled ? 'nothing gathered yet — run on existing items' : 'nothing gathered yet'}
              {(it.enabled_for?.length ?? 0) > 0 && ` · ${it.enabled_for!.join('/')}`}
            </div>
          </div>
          <span onClick={(e) => e.stopPropagation()}>
            {
}
            <Button size="sm" variant="ghost" ariaLabel={`Delete intent: ${rowSubject([it.goal || it.id], 40)}`}
              onClick={async () => {
                if (!(await confirmIntentDelete(it.goal || it.id, it.outcome_count ?? 0))) return
                if (!(await reportingWrite('delete this intent', () => api.deleteKnowledgeIntent(it.id)))) return
                load()
              }}><Trash2 size={14} /></Button>
          </span>
        </ListRow>
      ))}
    </div>
  )
}

function OutcomeCard({ o, onOpenItem }: { o: IntentOutcome; onOpenItem: (id: string) => void }) {
  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface-container p-m flex flex-col gap-s">
      {o.takeaway && <p data-type="body-s" className="text-on-surface">{o.takeaway}</p>}
      {(o.fields?.length ?? 0) > 0 && (
        <div data-type="body-s" className="grid grid-cols-[auto_1fr] gap-x-m gap-y-1">
          {o.fields!.map((f, i) => (
            <Fragment key={i}>
              <span className="text-on-surface-low">{f.name}</span>
              <OutcomeFieldValue field={f} />
            </Fragment>
          ))}
        </div>
      )}
      <button type="button" onClick={() => o.item_id && onOpenItem(o.item_id)} disabled={!o.item_id}
        data-type="caption" className="self-start inline-flex items-center gap-1 text-on-surface-low hover:text-primary disabled:hover:text-on-surface-low disabled:opacity-70">
        <FileText size={12} />
        {o.item_id ? (o.item_title || 'source item') : `${o.item_title || 'source item'} (removed — insight kept)`}
      </button>
    </div>
  )
}

function IntentDetail({ intent, onChanged, onClose, onOpenItem }: {
  intent: KnowledgeIntent
  onChanged: () => void
  onClose: () => void
  onOpenItem: (id: string) => void
}) {
  const [outcomes, setOutcomes] = useState<IntentOutcome[] | null>(null)
  const [outcomesErr, setOutcomesErr] = useState<unknown>(null)
  const [running, setRunning] = useState(false)
  const [genning, setGenning] = useState(false)
  const [updating, setUpdating] = useState(false)
  const [note, setNote] = useState('')
  const load = () => api.knowledgeIntentOutcomes(intent.id)
    .then((r) => { setOutcomesErr(null); setOutcomes(r.outcomes) })
    .catch((e) => { setOutcomesErr(e); setOutcomes(null) })
  useEffect(() => { load() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [intent.id])

  const run = async () => {
    setRunning(true); setNote('')
    try {
      const r = await api.runKnowledgeIntent(intent.id)
      setOutcomesErr(null); setOutcomes(r.outcomes)
      const errSuffix = r.errors ? ` (${r.errors} couldn't be evaluated — try again in a moment)` : ''
      setNote(
        r.new > 0 ? `Found ${r.new} new match${r.new === 1 ? '' : 'es'}.${errSuffix}`
        : r.matched > 0 ? `No new matches — ${r.matched} existing still match.${errSuffix}`
        : r.errors ? `Couldn't evaluate ${r.errors} item${r.errors === 1 ? '' : 's'} — the model may still be warming up. Try again in a moment.`
        : 'No matches in your existing items.')
      onChanged()
    } catch { setNote('Run failed.') } finally { setRunning(false) }
  }

  const generateSkill = async () => {
    setGenning(true); setNote('')
    try {
      const r = await api.generateSkillFromIntent(intent.id)
      setNote(`Created skill "${r.skill}" from ${outcomes?.length ?? 0} gathered item${(outcomes?.length ?? 0) === 1 ? '' : 's'}. Find it under Skills.`)
    } catch (e) { setNote(e instanceof Error ? e.message : 'Skill generation failed.') } finally { setGenning(false) }
  }

  const update = async (body: Pick<KnowledgeIntent, 'enabled'> | Pick<KnowledgeIntent, 'propose_skill'>) => {
    setUpdating(true); setNote('')
    try {
      await api.updateKnowledgeIntent(intent.id, body)
      onChanged()
    } catch (e) { setNote(e instanceof Error ? e.message : 'Intent update failed.') } finally { setUpdating(false) }
  }

  const hasOutcomes = (outcomes?.length ?? 0) > 0

  return (
    <div className="flex flex-col gap-m p-l">
      <p data-type="body-m" className="text-on-surface">{intent.goal}</p>
      <div className="flex flex-wrap items-center gap-s">
        {
}
        <Button size="sm" variant="secondary" onClick={run} loading={running} loadingLabel="Running…"><Play size={14} /> Run on existing items</Button>
        <Button size="sm" variant="secondary" onClick={() => update({ enabled: !intent.enabled })} disabled={updating}>
          {intent.enabled ? <Pause size={14} /> : <Play size={14} />} {intent.enabled ? 'Pause' : 'Resume'}
        </Button>
        <Button size="sm" variant="secondary" onClick={() => update({ propose_skill: !intent.propose_skill })} disabled={updating}>
          <Sparkles size={14} /> {intent.propose_skill ? 'Stop proposing skill' : 'Propose skill'}
        </Button>
        {intent.propose_skill && (
          <Button size="sm" variant="secondary" onClick={generateSkill}
            title="Synthesize a reusable skill from what this intent has gathered"
            loading={genning} loadingLabel="Generating…"
            disabled={genning || !hasOutcomes} disabledReason={!hasOutcomes && !genning ? 'Gather some matches first' : undefined}>
            <Sparkles size={14} /> Generate skill
          </Button>
        )}
        <span onClick={(e) => e.stopPropagation()}>
          <Button size="sm" variant="ghost" onClick={async () => {
            if (!(await confirmIntentDelete(intent.goal || intent.id, outcomes?.length ?? 0))) return
            if (!(await reportingWrite('delete this intent', () => api.deleteKnowledgeIntent(intent.id)))) return
            onChanged(); onClose()
          }}><Trash2 size={14} /> Delete</Button>
        </span>
      </div>
      {note && <p data-type="body-s" className="text-on-surface-low">{note}</p>}
      <div data-type="caption" className="text-on-surface-low uppercase tracking-wide">Gathered ({outcomes?.length ?? 0})</div>
      {outcomesErr ? <LoadError what="gathered matches" error={outcomesErr} onRetry={load} />
        : outcomes === null ? <ListSkeleton rows={3} what="gathered matches" />
        : outcomes.length === 0 ? <p data-type="body-s" className="text-on-surface-low">Nothing gathered yet. Save items relevant to this intent, or run it on what you already have.</p>
        : <div className="flex flex-col gap-s">{outcomes.map((o) => <OutcomeCard key={o.id} o={o} onOpenItem={onOpenItem} />)}</div>}
    </div>
  )
}

function EntityDetail({ name, onOpenItem, onSelectEntity }: { name: string; onOpenItem: (id: string) => void; onSelectEntity?: (name: string) => void }) {
  const { data: items, loading, error: itemsErr, refresh: refreshItems } = useQuery(`knowledge:entity-items:${name}`, () => api.knowledgeEntityItems(name))
  const { data: related } = useQuery(`knowledge:entity-related:${name}`, () => api.knowledgeEntityRelated(name).then((r) => r.related))
  return (
    <div className="flex flex-col gap-l p-l">
      {(related?.length ?? 0) > 0 && (
        <div className="flex flex-col gap-s">
          <div data-type="caption" className="text-on-surface-low uppercase tracking-wide">Connected to</div>
          <div className="flex flex-col gap-1">
            {related!.map((r, i) => (
              <button key={i} type="button" onClick={() => onSelectEntity?.(r.name)}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-surface-high">
                <Network size={13} className="shrink-0 text-primary/70" />
                <span data-type="body-s" className="truncate text-on-surface">{r.name}</span>
                <span data-type="caption" className="ml-auto shrink-0 text-on-surface-low">{r.outgoing ? '' : '← '}{r.relation_type}{r.outgoing ? ' →' : ''}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="flex flex-col gap-s">
        <div data-type="caption" className="text-on-surface-low uppercase tracking-wide">Mentioned in</div>
        { }
        {items === undefined && itemsErr ? <LoadError what="mentioned items" error={itemsErr} onRetry={refreshItems} />
          : items === undefined ? (loading ? <ListSkeleton rows={3} what="mentioned items" /> : null)
          : items.length === 0 ? <p data-type="body-s" className="text-on-surface-low">No items reference this entity.</p>
          : items.map((it, i) => {
              const tm = resolveType(it)
              return (
                <ListRow key={it.id} index={i} accent={tm.tone} onClick={() => onOpenItem(it.id)} label={it.title || it.url_title || '(untitled)'}>
                  <tm.icon size={16} style={{ color: tm.tone }} className="shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div data-type="body-s" className="truncate text-on-surface">{it.title || it.url_title || '(untitled)'}</div>
                    <div data-type="caption" className="truncate text-on-surface-low" style={{ color: tm.tone }}>{typeLabel(it)}</div>
                  </div>
                </ListRow>
              )
            })}
      </div>
    </div>
  )
}

function IntentEditor({ intent, onClose, onSaved }: { intent: KnowledgeIntent; onClose: () => void; onSaved: () => void }) {
  const [goal, setGoal] = useState(intent.goal ?? '')
  const [enabledFor, setEnabledFor] = useState((intent.enabled_for ?? []).join(', '))
  const [proposeSkill, setProposeSkill] = useState(!!intent.propose_skill)
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)

  async function save() {
    setErr('')
    const g = goal.trim()
    if (!g) { setErr('Describe what you want to track.'); return }
    setSaving(true)
    try {
      await api.upsertKnowledgeIntent({
        id: intent.id || undefined, goal: g, enabled: true, propose_skill: proposeSkill,
        enabled_for: enabledFor.split(',').map((s) => s.trim()).filter(Boolean),
      })
      onSaved()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }

  return (
    <div className="p-l flex flex-col gap-m">
      <div className="flex items-center justify-between">
        <span data-type="body-m" className="text-on-surface">New intent</span>
        {
}
        <button type="button" aria-label="Close the intent editor" onClick={onClose} className="text-on-surface-low hover:text-on-surface"><X size={16} /></button>
      </div>
      <div className="flex flex-col gap-1.5">
        <label data-type="caption" className="text-on-surface-low uppercase tracking-wide">What do you want to track?</label>
        <textarea aria-label="What do you want to track?" value={goal} onChange={(e) => setGoal(e.target.value)} rows={4} autoFocus
          placeholder={'e.g. "anything that could improve my homelab self-hosted setup"'}
          data-type="body-s" className="rounded-md bg-surface p-3 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary resize-none" />
        <p data-type="caption" className="text-on-surface-low">Plain language. As items are saved, Gideon decides what's relevant and pulls out the useful specifics for you — no need to define fields.</p>
      </div>
      <div className="flex flex-col gap-1.5">
        <label data-type="caption" className="text-on-surface-low uppercase tracking-wide">Limit to types (optional)</label>
        <input aria-label="Limit to types (optional)" value={enabledFor} onChange={(e) => setEnabledFor(e.target.value)} placeholder="comma-separated, blank = all types"
          data-type="body-s" className="h-9 rounded-md bg-surface px-3 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
      </div>
      <label data-type="body-s" className="flex items-start gap-2 text-on-surface-var">
        <input type="checkbox" className="mt-0.5" checked={proposeSkill} onChange={(e) => setProposeSkill(e.target.checked)} />
        <span>Offer to build a skill from this intent — adds a “Generate skill” action that distills what it has gathered into a reusable skill.</span>
      </label>
      {err && <FieldError>{err}</FieldError>}
      <div className="flex justify-end gap-s"><Button size="sm" variant="ghost" onClick={onClose}>Cancel</Button><Button size="sm" onClick={save} loading={saving} loadingLabel="Saving…">Save intent</Button></div>
    </div>
  )
}

function FilterChip({ active, onClick, tone, children }: { active: boolean; onClick: () => void; tone?: string; children: React.ReactNode }) {
  const selected = tone
    ? { background: `color-mix(in srgb, ${tone} 20%, transparent)`, color: tone }
    : { background: 'var(--color-primary-container)', color: 'var(--color-on-primary-container)' }
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
      data-type="body-s" className="inline-flex items-center gap-1 rounded-pill px-m h-8 transition-colors"
      style={active ? selected : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }}>
      {children}
    </button>
  )
}
