import { useEffect, useState } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import { AlertTriangle, Pencil, Trash2, Check, X, ExternalLink, Sparkles, Layers, Loader2, Pin, Star, BookOpen, BookOpenText, Archive, Download, Target, Maximize2, Wand2, ChevronDown, WifiOff, RefreshCw, MessageCircleQuestion } from 'lucide-react'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { useFocusTrap } from '../../shared/ui/useFocusTrap'
import { investigate } from '../../shared/data/investigate'
import { Button } from '../../shared/ui/Button'
import { Markdown } from '../../shared/ui/Markdown'
import { ChipInput, FieldError } from '../../shared/ui/forms'
import type { KnowledgeAnnotation, KnowledgeItem, IntentOutcome, IntentOutcomeField, KnowledgeStaleness } from '../../shared/data/api'
import { ReadingView } from './ReadingView'
import { resolveType, insightRows, fmtBytes, relTime, GIST_LANGUAGES } from './knowledgeMeta'
import { getKnowledge, updateKnowledge, deleteKnowledge } from './knowledgeStore'
import { GistEditor } from './GistEditor'
import { confirm } from '../../shared/ui/dialog'
import { api } from '../../shared/data/api'
import { BUSY_REASON } from '../../shared/ui/unavailable'

function gistFence(code: string, lang?: string): string {
  const longest = Math.max(0, ...(code.match(/`+/g) || []).map((r) => r.length))
  const fence = '`'.repeat(Math.max(3, longest + 1))
  return `${fence}${(lang || '').trim()}\n${code}\n${fence}`
}

const SYNTHESIZED_KINDS = new Set(['insight', 'report', 'overview'])

function StaleSynthesisBanner({ item }: { item: KnowledgeItem }) {
  const [state, setState] = useState<KnowledgeStaleness | null>(null)
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState('')

  useEffect(() => {
    let alive = true
    setState(null); setOutcome('')
    if (!SYNTHESIZED_KINDS.has(item.item_type ?? '')) return
    api.knowledgeStaleness(item.id).then((s) => { if (alive) setState(s) }).catch(() => {})
    return () => { alive = false }
  }, [item.id, item.item_type])

  if (!state?.stale) return null
  const n = state.new_source_items
  const changed = state.changed_sources
  const parts = [
    n > 0 ? `${n} new source item${n === 1 ? '' : 's'}` : '',
    changed > 0 ? `${changed} cited source${changed === 1 ? '' : 's'} edited` : '',
  ].filter(Boolean)

  return (
    <div role="status" className="flex shrink-0 flex-wrap items-center gap-s rounded-lg border border-outline-variant/60 bg-surface-container/60 px-3 py-2">
      <AlertTriangle size={14} className="shrink-0" style={{ color: 'var(--color-warning)' }} />
      <span data-type="body-s" className="min-w-0 text-on-surface-var">
        {parts.join(' \u00b7 ')} since this was written{state.scope ? ` (${state.scope})` : ''}.
      </span>
      <Button size="xs" variant="secondary" className="ml-auto" disabled={busy} disabledReason={BUSY_REASON}
        ariaLabel="Regenerate this synthesis as a proposal"
        onClick={() => {
          setBusy(true); setOutcome('')
          api.knowledgeRegenerate(item.id)
            .then((r) => {
              const p = r.proposal ?? {}
              if (r.already_pending) return setOutcome('Already queued — accept it in the proposal queue.')
              if (p.pending) return setOutcome('Queued — accept it in the proposal queue to apply.')
              return setOutcome(p.reason || (p.applied ? 'Applied.' : 'Nothing to regenerate.'))
            })
            .catch((e) => setOutcome(String((e as Error)?.message || e)))
            .finally(() => setBusy(false))
        }}>
        Regenerate
      </Button>
      {outcome && <span data-type="caption" className="w-full text-on-surface-low">{outcome}</span>}
    </div>
  )
}

export function KnowledgeDetail({ item, onChanged, onDeleted, onTagClick, onShowDetails, detailsOpen, detailsCount, onHeader, reading = false, onToggleReading, annotations = [], onAnnotationsChanged, insightRail }: { item: KnowledgeItem; onChanged: () => void; onDeleted: () => void; onTagClick?: (tag: string) => void; onShowDetails?: () => void; detailsOpen?: boolean; detailsCount?: number; onHeader?: (parts: { wand: React.ReactNode; actions: React.ReactNode; editing: boolean } | null) => void; reading?: boolean; onToggleReading?: () => void; annotations?: KnowledgeAnnotation[]; onAnnotationsChanged?: () => void;   insightRail?: React.ReactNode }) {
  const [full, setFull] = useState<KnowledgeItem>(item)
  const [editing, setEditing] = useState(false)
  const [knownTags, setKnownTags] = useState<string[]>([])
  const startEdit = async () => {
    if (!knownTags.length) api.knowledgeTags().then(setKnownTags).catch(() => {})
    let src = full
    if (src.content_truncated || item.content_truncated) {
      const fresh = await getKnowledge(item.id)
      if (fresh) { src = fresh; setFull(fresh) }
    }
    setDraft({ title: src.title ?? '', content: src.content ?? '', summary: src.summary ?? '', tags: src.tags ?? [], item_type: src.item_type ?? src.type ?? 'note', gist_language: src.gist_language ?? '', url: src.url ?? '' })
    setEditing(true)
  }
  const [draft, setDraft] = useState({ title: item.title ?? '', content: item.content ?? '', summary: item.summary ?? '', tags: item.tags ?? [], item_type: item.item_type ?? item.type ?? 'note', gist_language: item.gist_language ?? '', url: item.url ?? '' })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const tm = resolveType(full)

  const [itemIntents, setItemIntents] = useState<IntentOutcome[]>([])
  const [nodePhases, setNodePhases] = useState<Record<string, string>>({})
  const [procStatus, setProcStatus] = useState<string>(item.processing_status ?? '')
  const [ingestGraph, setIngestGraph] = useState<import('../../shared/data/api').KnowledgeIngestGraph | null>(null)
  const [fullscreen, setFullscreen] = useState<{ title: string; node: React.ReactNode } | null>(null)
  const [insightsOpen, setInsightsOpen] = useState(false)
  const [reingest, setReingest] = useState(true)

  useEffect(() => {
    let alive = true
    setEditing(false); setItemIntents([]); setNodePhases({}); setIngestGraph(null)
    getKnowledge(item.id).then((d) => { if (alive && d) { setFull(d); setProcStatus(d.processing_status ?? ''); setDraft({ title: d.title ?? '', content: d.content ?? '', summary: d.summary ?? '', tags: d.tags ?? [], item_type: d.item_type ?? d.type ?? 'note', gist_language: d.gist_language ?? '', url: d.url ?? '' }) } }).catch(() => setFull(item))
    api.knowledgeItemIntents(item.id).then((r) => { if (alive) setItemIntents(r.outcomes || []) }).catch(() => {})
    api.knowledgeItemGraph(item.id).then((g) => { if (alive) setIngestGraph(g) }).catch(() => {})
    return () => { alive = false }
  }, [item.id])

  useEffect(() => {
    if (procStatus !== 'queued' && procStatus !== 'processing') return
    const es = new EventSource(api.knowledgeIngestStreamUrl(item.id))
    const onNode = (e: MessageEvent) => {
      try { const d = JSON.parse(e.data); if (d.node) setNodePhases((p) => ({ ...p, [d.node]: d.phase })) } catch {   }
    }
    const onComplete = (e: MessageEvent) => {
      try { const d = JSON.parse(e.data); setProcStatus(d.status || 'done') } catch { setProcStatus('done') }
      getKnowledge(item.id).then((d) => d && setFull(d)).catch(() => {})
      api.knowledgeItemIntents(item.id).then((r) => setItemIntents(r.outcomes || [])).catch(() => {})
      onChanged()
      es.close()
    }
    es.addEventListener('node', onNode)
    es.addEventListener('ingest_complete', onComplete)
    es.addEventListener('ingest_failed', onComplete)
    es.onerror = () => es.close()
    return () => es.close()
  }, [item.id, procStatus])

  const insights = insightRows(full.insights)
  const [genning, setGenning] = useState(false)
  const canGenerate = !!(full.content || '').trim()
  const readable = canGenerate
  const readingMode = reading && readable

  async function generateInsights() {
    setGenning(true); setErr('')
    try {
      const updated = await api.generateKnowledgeIntelligence(item.id)
      setFull(updated)
      setProcStatus(updated.processing_status ?? 'queued')
      onChanged()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Insight generation failed') } finally { setGenning(false) }
  }

  async function save() {
    setSaving(true); setErr('')
    try {
      const fields: Record<string, unknown> = {}
      if (draft.title !== (full.title ?? '')) fields.title = draft.title
      if (draft.content !== (full.content ?? '')) fields.content = draft.content
      if (draft.url !== (full.url ?? '')) fields.url = draft.url
      if (draft.summary !== (full.summary ?? '')) fields.summary = draft.summary
      if (draft.item_type !== (full.item_type ?? full.type)) { fields.type = draft.item_type; fields.item_type = draft.item_type }
      if ((draft.item_type === 'gist') && draft.gist_language !== (full.gist_language ?? '')) fields.gist_language = draft.gist_language
      const sameTags = (a: string[], b: string[]) => {
        if (a.length !== b.length) return false
        const sortedB = [...b].sort()
        return [...a].sort().every((t, i) => t === sortedB[i])
      }
      if (!sameTags(draft.tags ?? [], full.tags ?? [])) fields.tags = draft.tags
      if (Object.keys(fields).length === 0) { setEditing(false); return }
      const changedBody = 'content' in fields || 'url' in fields
      if (changedBody && !reingest) fields.reingest = false
      await updateKnowledge(item.id, fields)
      const { reingest: _r, ...applied } = fields
      setFull((f) => ({ ...f, ...applied }))
      if (changedBody && reingest) setProcStatus('queued')
      onChanged(); setEditing(false)
    } catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }
  async function del() {
    const highlights = annotations.length > 0
      ? ` Its ${annotations.length} highlight${annotations.length === 1 ? '' : 's'} ${annotations.length === 1 ? 'goes' : 'go'} with it.`
      : ''
    const stored = item.file_path ? ' The file stored in your library is deleted too.' : ''
    if (!(await confirm({
      title: `Delete "${full.title || 'this item'}"?`,
      body: `This removes it from the knowledge base.${highlights}${stored}`,
      danger: true,
      confirmLabel: 'Delete',
    }))) return
    try { await deleteKnowledge(item.id); onDeleted() } catch { setErr('Delete failed') }
  }
  async function toggleFlag(flag: 'is_pinned' | 'is_archived') {
    const next = !full[flag]
    setFull((f) => ({ ...f, [flag]: next }))
    try { await updateKnowledge(item.id, { [flag]: next }); onChanged() }
    catch { setFull((f) => ({ ...f, [flag]: !next })); setErr('Update failed') }
  }
  async function cycleReadState() {
    const cur = full.read_state || 'unread'
    const next = cur === 'reading' ? 'read' : cur === 'read' ? 'unread' : 'reading'
    setFull((f) => ({ ...f, read_state: next }))
    try { await api.setKnowledgeReadState(item.id, next); onChanged() }
    catch { setFull((f) => ({ ...f, read_state: cur })); setErr('Update failed') }
  }

  async function toggleFavorite() {
    const next = !full.favorited
    setFull((f) => ({ ...f, favorited: next }))
    try { await api.setKnowledgeFavorited(item.id, next); onChanged() }
    catch { setFull((f) => ({ ...f, favorited: !next })); setErr('Update failed') }
  }

  async function applyAiTitle() {
    const ai = (full.ai_title || '').trim()
    if (!ai) return
    const prev = full.title
    setFull((f) => ({ ...f, title: ai }))
    try { await updateKnowledge(item.id, { title: ai }); onChanged() }
    catch { setFull((f) => ({ ...f, title: prev })); setErr('Update failed') }
  }
  const aiTitleAvailable = !!(full.ai_title && full.ai_title.trim() && full.ai_title.trim() !== (full.title || '').trim())

  const actionCluster = (
    <HeaderActions>
      <HeaderControl icon={Trash2} label="Delete" priority="low" danger onClick={del} />
      {editing ? (
        <>
          <HeaderControl icon={X} label="Cancel" onClick={() => { setEditing(false); setErr('') }} />
          <HeaderControl icon={Check} label={saving ? 'Saving…' : 'Save'} variant="primary" priority="primary" onClick={save} disabled={saving} />
        </>
      ) : (
        <HeaderControl icon={Pencil} label="Edit" variant="primary" priority="primary" onClick={startEdit} />
      )}
      {
}
      <HeaderControl icon={MessageCircleQuestion} label="Investigate in chat" priority="low"
        onClick={() => { void investigate('knowledge_item', full.id, { backLink: `#/knowledge/item/${full.id}` }) }} />
      {
}
      {onToggleReading && (
        <HeaderControl icon={BookOpenText} label="Reading mode" active={reading}
          disabled={!readable} hint={readable ? undefined : 'This item has no text body to read'}
          onClick={onToggleReading} />
      )}
      <HeaderControl icon={BookOpen}
        label={(full.read_state || 'unread') === 'reading' ? 'Reading — mark read'
          : full.read_state === 'read' ? 'Read — mark unread' : 'Mark as reading'}
        active={(full.read_state || 'unread') !== 'unread'} onClick={cycleReadState} />
      {
}
      <HeaderControl icon={Star} label="Favorite" active={!!full.favorited} onClick={toggleFavorite} />
      <HeaderControl icon={Pin} label="Pin" active={full.is_pinned} onClick={() => toggleFlag('is_pinned')} />
      <HeaderControl icon={Archive} label="Archive" active={full.is_archived} onClick={() => toggleFlag('is_archived')} />
      {onShowDetails && (
        <HeaderControl icon={Layers} label={`More details${detailsCount ? ` · ${detailsCount}` : ''}`} active={detailsOpen} priority="low" onClick={onShowDetails} />
      )}
    </HeaderActions>
  )
  const wandBtn = aiTitleAvailable ? (
    <button type="button" onClick={applyAiTitle} aria-label="Use the AI-suggested title"
      title={`Use AI title: "${full.ai_title}"`}
      className="grid size-6 shrink-0 place-items-center rounded text-primary/70 hover:bg-surface-high hover:text-primary transition-colors">
      <Wand2 size={14} />
    </button>
  ) : null

  useEffect(() => {
    if (!onHeader) return
    onHeader({ wand: wandBtn, actions: actionCluster, editing })
    return () => onHeader(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [full, draft, reingest, aiTitleAvailable, detailsCount, detailsOpen, editing, saving, reading])

  const titleEditable = draft.item_type !== 'fleeting' && draft.item_type !== 'journal'
  const localToday = (() => {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  })()
  const journalLocked = (full.item_type ?? full.type) === 'journal'
    && (full.created_at || '').slice(0, 10) !== localToday
  const reingestApplies = tm.group === 'text' || tm.key === 'bookmark'
  const editBar = reingestApplies ? (
    <label data-type="body-s" className="flex items-center gap-1.5 text-on-surface-var cursor-pointer select-none">
      <input type="checkbox" checked={reingest} onChange={(e) => setReingest(e.target.checked)} className="size-4 accent-[var(--color-primary)]" />
      Re-process on save
    </label>
  ) : null

  if (editing) {
    return (
      <>
      <div className="flex h-full min-h-0 flex-col gap-l">
        {err && <FieldError className="shrink-0">{err}</FieldError>}
        {titleEditable && (
          <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} autoFocus placeholder="Title"
            className="shrink-0 w-full bg-transparent text-on-surface outline-none border-b border-outline-variant/40 pb-1.5 text-[1.0625rem] focus:border-primary" data-type="title-l" />
        )}
        {draft.item_type === 'gist' && (
          <div className="shrink-0 flex items-center gap-2">
            <span data-type="caption" className="text-on-surface-low uppercase tracking-wide">Language</span>
            <select value={draft.gist_language || ''} onChange={(e) => setDraft({ ...draft, gist_language: e.target.value })}
              data-type="body-s" className="h-8 appearance-none rounded-md bg-surface-container px-m text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary">
              <option value="">(none)</option>
              {GIST_LANGUAGES.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          </div>
        )}
        { }
        {tm.key === 'bookmark' && (
          <div className="shrink-0 flex items-center gap-s rounded-md bg-surface-container px-m h-10 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
            <ExternalLink size={15} className="shrink-0 text-on-surface-low" />
            <input value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} placeholder="https://…"
              data-type="body-m" className="flex-1 bg-transparent text-on-surface outline-none placeholder:text-on-surface-low" />
          </div>
        )}
        { }
        <div className="shrink-0"><ChipInput values={draft.tags} onChange={(v) => setDraft({ ...draft, tags: v })} placeholder="Add a tag, Enter" suggestions={knownTags} /></div>
        {
}
        {journalLocked && <p data-type="caption" className="shrink-0 text-on-surface-low">This journal entry is immutable — its day has passed. You can still curate tags, pin, and archive.</p>}
        {draft.item_type === 'gist' ? (
          <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-outline-variant/40 bg-surface-container">
            <GistEditor value={draft.content} onChange={(v) => setDraft({ ...draft, content: v })} language={draft.gist_language} />
          </div>
        ) : tm.group === 'text' ? (
          <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-outline-variant/40 bg-surface-container focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
            <textarea value={draft.content} onChange={(e) => setDraft({ ...draft, content: e.target.value })} readOnly={journalLocked}
              placeholder="Markdown supported…"
              data-type="body-s" className={`h-full w-full resize-none bg-transparent px-m py-2 text-on-surface leading-relaxed outline-none ${journalLocked ? 'opacity-60 cursor-not-allowed' : ''}`} />
          </div>
        ) : (
          <div className="relative flex min-h-0 flex-1 overflow-hidden">
            <Preview item={full} tm={tm} prominent />
          </div>
        )}
      </div>
      {editBar && <div className="-mx-l shrink-0 border-t border-outline-variant/40 bg-surface/95 px-l py-3">{editBar}</div>}
      </>
    )
  }

  return (
    <>
    {fullscreen && <FullscreenModal title={fullscreen.title} onClose={() => setFullscreen(null)}>{fullscreen.node}</FullscreenModal>}
    <div className="flex h-full min-h-0 flex-col gap-l">
      {
}
      {err && <FieldError>{err}</FieldError>}

      { }
      <StaleSynthesisBanner item={full} />

      {
}
      <div className="flex flex-wrap shrink-0 items-start gap-x-m gap-y-1">
        <div data-type="body-s" className="flex flex-wrap items-center gap-x-m gap-y-1 text-on-surface-low min-w-0">
          {full.provider && full.provider !== 'native' && <span data-type="caption" className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var">{full.provider}</span>}
          {full.mime_type && <span data-type="caption" className="font-mono">{full.mime_type}</span>}
          {full.file_size != null && <span>{fmtBytes(full.file_size)}</span>}
          {full.file_metadata?.width && full.file_metadata?.height && <span>{full.file_metadata.width}×{full.file_metadata.height}</span>}
          {typeof full.file_metadata?.page_count === 'number' && <span>{full.file_metadata.page_count} pages</span>}
          {typeof full.file_metadata?.sheet_count === 'number' && <span>{full.file_metadata.sheet_count} sheet{full.file_metadata.sheet_count === 1 ? '' : 's'}</span>}
          {typeof full.file_metadata?.slide_count === 'number' && <span>{full.file_metadata.slide_count} slide{full.file_metadata.slide_count === 1 ? '' : 's'}</span>}
          {typeof full.file_metadata?.row_count === 'number' && full.file_metadata.row_count > 0 && <span>{full.file_metadata.row_count} row{full.file_metadata.row_count === 1 ? '' : 's'}</span>}
          {full.word_count != null && full.word_count > 0 && <span>{full.word_count} words</span>}
          {full.updated_at && <span title="Last updated">{relTime(full.updated_at)}</span>}
        </div>
        {
}
        <div className="ml-auto min-w-0">
          <ProcessingStrip status={procStatus} nodePhases={nodePhases} error={full.processing_error} graph={ingestGraph} onRetry={generateInsights} retrying={genning} />
        </div>
      </div>

      {
}
      <div className="flex shrink-0 items-start gap-s">
        <div className="flex flex-1 flex-wrap gap-1.5">{(full.tags ?? []).map((t) => onTagClick
          ? <button key={t} type="button" onClick={() => onTagClick(t)} title={`Find items tagged "${t}"`} data-type="caption" className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var transition-colors hover:bg-surface-container hover:text-primary">{t}</button>
          : <span key={t} data-type="caption" className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var">{t}</span>)}</div>
        <ExpandButton onClick={() => setFullscreen({ title: full.title || full.url_title || 'Preview', node: <Preview item={full} tm={tm} fullscreen /> })} />
      </div>

      {
}
      {
}
      {readingMode ? (
        <ReadingView item={full} annotations={annotations}
          onAnnotationsChanged={onAnnotationsChanged ?? (() => {})} insightRail={insightRail}
          onRestructured={onChanged} />
      ) : (
        <>
          <Preview item={full} tm={tm} prominent />
          <InsightsDock
            open={insightsOpen} onToggle={() => setInsightsOpen((v) => !v)}
            summary={full.summary} insights={insights} intents={itemIntents}
            canGenerate={canGenerate} genning={genning} onGenerate={generateInsights}
            processing={procStatus === 'queued' || procStatus === 'processing'}
          />
        </>
      )}
    </div>
    </>
  )
}

function Preview({ item, tm, fullscreen, prominent }: { item: KnowledgeItem; tm: ReturnType<typeof resolveType>; fullscreen?: boolean; prominent?: boolean }) {
  const baseFrame = 'rounded-lg border border-outline-variant/40 bg-surface-container overflow-hidden'
  const fillFrame = baseFrame + (prominent ? ' flex w-full min-h-0 flex-1 flex-col' : '')
  const hasFile = !!item.file_path
  const mediaMax = fullscreen ? 'max-h-[80vh]' : prominent ? 'min-h-0 flex-1' : 'max-h-80'
  const textMax = fullscreen ? '' : prominent ? 'min-h-0 flex-1 overflow-y-auto' : 'max-h-80 overflow-y-auto'

  if (tm.key === 'bookmark' || item.url) {
    return (
      <div className={baseFrame + (prominent ? ' w-full self-start' : '')}>
        <a href={item.url} target="_blank" rel="noreferrer" className="flex items-center gap-s px-m py-3 hover:bg-surface-high transition-colors">
          <tm.icon size={18} className="shrink-0" style={{ color: tm.tone }} />
          <div className="flex-1 min-w-0">
            <div data-type="body-s" className="truncate text-on-surface">{item.url_title || item.title || item.url}</div>
            {item.url && <div data-type="caption" className="truncate text-on-surface-low">{item.url}</div>}
          </div>
          <ExternalLink size={14} className="shrink-0 text-on-surface-low" />
        </a>
      </div>
    )
  }
  if (hasFile && (tm.key === 'image' || tm.key === 'audio' || tm.key === 'video')) {
    const fills = tm.key === 'image' || tm.key === 'video'
    return (
      <div className={fills ? fillFrame : baseFrame + (prominent ? ' w-full self-start' : '')}>
        {tm.key === 'image' && <img src={api.knowledgeItemFileUrl(item.id)} alt={item.title} className={`${mediaMax} w-full object-contain bg-surface`} />}
        {tm.key === 'audio' && <div className="p-m"><audio src={api.knowledgeItemFileUrl(item.id)} controls className="w-full" /></div>}
        {tm.key === 'video' && <video src={api.knowledgeItemFileUrl(item.id)} controls className={`${mediaMax} w-full bg-black`} />}
        <FileRow item={item} tm={tm} />
      </div>
    )
  }
  if (hasFile) {
    const hasText = !!(item.content || '').trim()
    return (
      <div className={hasText ? fillFrame : baseFrame + (prominent ? ' w-full self-start' : '')}>
        <FileRow item={item} tm={tm} />
        {hasText && (
          <div data-type="body-s" className={`border-t border-outline-variant/40 px-m py-2 text-on-surface-var leading-relaxed ${textMax}`}>
            <Markdown>{item.content!}</Markdown>
          </div>
        )}
      </div>
    )
  }
  if (item.content && tm.key === 'gist') {
    return (
      <div className={`${prominent ? fillFrame : baseFrame} px-m ${textMax}`}>
        <Markdown>{gistFence(item.content, item.gist_language)}</Markdown>
      </div>
    )
  }
  if (item.content) {
    return (
      <div className={`${prominent ? fillFrame : baseFrame} px-m py-2 ${textMax}`}>
        <div data-type="body-s" className="text-on-surface-var leading-relaxed"><Markdown>{item.content}</Markdown></div>
      </div>
    )
  }
  return null
}

function FileRow({ item, tm }: { item: KnowledgeItem; tm: ReturnType<typeof resolveType> }) {
  return (
    <a href={api.knowledgeItemFileUrl(item.id)} target="_blank" rel="noreferrer" download={item.title}
      data-type="body-s" className="flex items-center gap-s px-m py-2.5 hover:bg-surface-high transition-colors text-on-surface-var border-t border-outline-variant/30 first:border-t-0">
      <tm.icon size={16} style={{ color: tm.tone }} className="shrink-0" />
      <span className="flex-1 truncate">{item.title}</span>
      {item.file_size != null && <span data-type="caption" className="shrink-0 text-on-surface-low">{fmtBytes(item.file_size)}</span>}
      <Download size={14} className="shrink-0 text-on-surface-low" />
    </a>
  )
}

type NodePhase = 'pending' | 'running' | 'done' | 'skipped' | 'failed'

function resolveNodePhases(
  graph: import('../../shared/data/api').KnowledgeIngestGraph,
  live: Record<string, string>,
  status: string,
  error?: string,
): Record<string, NodePhase> {
  const persisted = graph.node_phases || {}
  const skipOnly = (error || '').startsWith('Skipped (optional steps unavailable):')
  const skipped = new Set(skipOnly ? error!.split(':').slice(1).join(':').split(',').map((s) => s.trim()) : [])
  const hasRealFailure = !skipOnly && !!error && (status === 'failed' || status === 'unreachable' || status === 'partial')
  const failed = new Set(hasRealFailure ? error!.split(';').map((s) => s.split(':')[0].trim()).filter(Boolean) : [])
  const out: Record<string, NodePhase> = {}
  for (const n of graph.nodes) {
    const nt = n.node_type
    const lv = live[nt]
    if (lv === 'running' || lv === 'done' || lv === 'skipped' || lv === 'failed') { out[nt] = lv as NodePhase; continue }
    const p = persisted[nt]
    if (p === 'done' || p === 'skipped' || p === 'failed') { out[nt] = p as NodePhase; continue }
    if (failed.has(nt)) { out[nt] = 'failed'; continue }
    if (skipped.has(nt)) { out[nt] = 'skipped'; continue }
    if (Object.keys(persisted).length) out[nt] = 'pending'
    else if (status === 'done' || status === 'partial') out[nt] = 'done'
    else out[nt] = 'pending'
  }
  return out
}

function dagLevels(graph: import('../../shared/data/api').KnowledgeIngestGraph): Map<string, number> {
  const preds = new Map<string, string[]>()
  for (const n of graph.nodes) preds.set(n.node_type, [])
  for (const e of graph.edges) { if (!e.loop) preds.get(e.to)?.push(e.from) }
  const level = new Map<string, number>()
  const visit = (nt: string, seen: Set<string>): number => {
    if (level.has(nt)) return level.get(nt)!
    if (seen.has(nt)) return 0
    seen.add(nt)
    const ps = preds.get(nt) || []
    const lv = ps.length ? Math.max(...ps.map((p) => visit(p, seen))) + 1 : 0
    level.set(nt, lv)
    return lv
  }
  for (const n of graph.nodes) visit(n.node_type, new Set())
  return level
}

function ProcessingStrip({ status, nodePhases, error, graph, onRetry, retrying }: { status: string; nodePhases: Record<string, string>; error?: string; graph?: import('../../shared/data/api').KnowledgeIngestGraph | null; onRetry?: () => void; retrying?: boolean }) {
  const active = status === 'queued' || status === 'processing'
  const unreachable = status === 'unreachable'
  if (!active && status !== 'partial' && status !== 'failed' && !unreachable && !graph) return null
  const hasDag = !!graph && graph.nodes.length > 0
  const retryBtn = onRetry && (unreachable || status === 'failed') ? (
    <button type="button" onClick={onRetry} disabled={retrying}
      data-type="caption" className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var transition-colors hover:text-on-surface disabled:opacity-60">
      <RefreshCw size={11} className={retrying ? 'animate-spin' : ''} /> {retrying ? 'Retrying…' : 'Retry'}
    </button>
  ) : null

  if (!hasDag) {
    if (unreachable) {
      return (
        <div data-type="body-s" className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1">
          <span className="inline-flex items-center gap-1.5" style={{ color: 'var(--color-warning)' }}><WifiOff size={13} /> Unreachable</span>
          {retryBtn}
          {error && <span data-type="caption" className="basis-full text-on-surface-low">{error}</span>}
        </div>
      )
    }
    if (active) {
      return (
        <div data-type="body-s" className="flex items-center gap-1.5 py-1" style={{ color: 'var(--color-primary)' }}>
          <Loader2 size={13} className="animate-spin" />
          <span>{status === 'queued' ? 'Queued…' : 'Processing…'}</span>
        </div>
      )
    }
    return (
      <div data-type="body-s" className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1">
        {error && <span data-type="caption" style={{ color: status === 'failed' ? 'var(--color-danger)' : 'var(--color-on-surface-low)' }}>{error}</span>}
        {retryBtn}
      </div>
    )
  }

  return (
    <div data-type="body-s" className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1">
      <MiniDag graph={graph!} phases={resolveNodePhases(graph!, nodePhases, status, error)} status={status} />
      {retryBtn}
      {error && <span data-type="caption" className="basis-full" style={{ color: status === 'failed' ? 'var(--color-danger)' : 'var(--color-on-surface-low)' }}>{error}</span>}
    </div>
  )
}

function nodeLabel(nodeType: string): string {
  const s = nodeType.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

const _dagDotColor = (p: NodePhase) => p === 'done' ? 'var(--color-success)'
  : p === 'failed' ? 'var(--color-danger)'
  : p === 'running' ? 'var(--color-primary)'
  : p === 'skipped' ? 'var(--color-on-surface-low)'
  : 'var(--color-outline-variant)'

function MiniDag({ graph, phases }: { graph: import('../../shared/data/api').KnowledgeIngestGraph; phases: Record<string, NodePhase>; status: string }) {
  const level = dagLevels(graph)
  const maxLevel = Math.max(0, ...[...level.values()])
  const columns: { node_type: string; backend?: string; model_backed?: boolean }[][] = Array.from({ length: maxLevel + 1 }, () => [])
  for (const n of graph.nodes) columns[level.get(n.node_type) ?? 0].push(n)

  const loopByTarget = new Map<string, { from: string; max: number }>()
  for (const e of graph.edges) if (e.loop) loopByTarget.set(e.to, { from: e.from, max: e.max_iters ?? 1 })

  const Dot = ({ nt }: { nt: string }) => {
    const ph = phases[nt] ?? 'pending'
    const c = _dagDotColor(ph)
    const loop = loopByTarget.get(nt)
    const stateWord = ph === 'running' ? 'processing' : ph
    return (
      <span className="inline-flex items-center gap-1" title={`${nt.replace(/_/g, ' ')}: ${ph}${loop ? ` (⟲ resamples up to ${loop.max}×)` : ''}`}>
        <span className="grid size-3.5 shrink-0 place-items-center rounded-full"
          style={{ border: `1.5px ${ph === 'pending' ? 'dashed' : 'solid'} ${c}`,
                   background: ph === 'done' ? 'color-mix(in srgb, var(--color-success) 22%, transparent)' : 'transparent' }}>
          {ph === 'running' && <Loader2 size={8} className="animate-spin" style={{ color: c }} />}
          {ph === 'done' && <Check size={8} strokeWidth={3} style={{ color: c }} />}
          {ph === 'failed' && <X size={8} style={{ color: c }} />}
        </span>
        <span data-type="caption" className="whitespace-nowrap" style={{ color: ph === 'pending' ? 'var(--color-on-surface-low)' : c }}>
          {nodeLabel(nt)}{loop && <RefreshCw size={9} className="ml-0.5 inline-block align-[-1px]" style={{ color: 'var(--color-primary)' }} />}
        </span>
        <span className="sr-only">{stateWord}</span>
      </span>
    )
  }

  return (
    <span className="inline-flex flex-wrap items-stretch gap-x-1 gap-y-1 align-middle">
      {columns.map((col, ci) => (
        <span key={ci} className="inline-flex items-center gap-1">
          {ci > 0 && <span className="h-px w-2.5 shrink-0 self-center" style={{ background: 'var(--color-outline-variant)' }} />}
          { }
          <span className="inline-flex flex-col justify-center gap-0.5">
            {col.map((n) => <Dot key={n.node_type} nt={n.node_type} />)}
          </span>
        </span>
      ))}
    </span>
  )
}

function Section({ label, icon: Icon, action, children }: { label: string; icon?: typeof Sparkles; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <div data-type="caption" className="mb-1.5 flex items-center gap-1.5 text-on-surface-low uppercase tracking-wide">{Icon && <Icon size={12} />}{label}{action && <span className="ml-auto normal-case tracking-normal">{action}</span>}</div>
      {children}
    </div>
  )
}

function InsightsDock({ open, onToggle, summary, insights, intents, canGenerate, genning, onGenerate, processing }: {
  open: boolean; onToggle: () => void; summary?: string
  insights: Array<{ label: string; value: string }>
  intents: IntentOutcome[]
  canGenerate: boolean; genning: boolean; onGenerate: () => void; processing?: boolean
}) {
  const hasMore = insights.length > 0 || intents.length > 0
  if (!summary && !hasMore && !canGenerate) return null
  const showGenerate = canGenerate && !processing
  return (
    <div className="mt-auto shrink-0 rounded-t-lg border border-b-0 border-outline-variant/40 bg-surface-container overflow-hidden">
      <div className="px-m">
        {
}
        {
}
        <div className="group/dock flex w-full items-center gap-2.5 py-3">
          {
}
          {
}
          <button type="button" onClick={hasMore ? onToggle : undefined} aria-disabled={!hasMore || undefined}
            aria-expanded={hasMore ? open : undefined}
            title={!hasMore ? 'Nothing more to show' : undefined}
            className="flex min-w-0 flex-1 items-center gap-2.5 text-left disabled:cursor-default aria-disabled:cursor-default">
            <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-primary/10">
              <Sparkles size={14} className={`text-primary ${genning || processing ? 'animate-pulse' : ''}`} />
            </span>
            <span data-type="label-s" className="shrink-0 text-on-surface" style={fvs(500)}>Insights</span>
            {hasMore && (
              <span data-type="caption" className="shrink-0 rounded-pill bg-surface-high px-1.5 text-on-surface-low tabular-nums">{insights.length + intents.length}</span>
            )}
            {
}
            <span data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface-low">
              {open ? '' : (summary || (processing ? 'Enriching…' : hasMore ? 'Key points, topics & more' : 'No insights yet'))}
            </span>
            {
}
            {hasMore && (
              <span aria-hidden className="grid size-7 shrink-0 place-items-center rounded-lg text-on-surface-low transition-colors group-hover/dock:bg-surface-high group-hover/dock:text-on-surface">
                <ChevronDown size={16} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
              </span>
            )}
          </button>
          {showGenerate && (
            <Button variant="ghost" size="sm" onClick={onGenerate} className="shrink-0"
              title="Reprocess this item — refreshes insights, entities, tags, and the embedding">
              <Sparkles size={12} className={genning ? 'animate-pulse' : ''} /> {genning ? 'Reprocessing…' : insights.length > 0 || summary ? 'Regenerate' : 'Generate'}
            </Button>
          )}
        </div>

        {open && (
          <div className="flex max-h-[45vh] flex-col gap-l overflow-y-auto border-t border-outline-variant/40 pb-m pt-m">
            {summary && <p data-type="body-s" className="text-on-surface-var leading-relaxed">{summary}</p>}
            {insights.length > 0 && (
              <Section label="Highlights" icon={Sparkles}>
                <div className="flex flex-col gap-1.5">
                  {insights.map((r) => (
                    <div key={r.label} className="rounded-md bg-surface-high px-m py-1.5">
                      <div data-type="caption" className="text-on-surface-low uppercase tracking-wide">{r.label}</div>
                      <div data-type="body-s" className="text-on-surface-var mt-0.5">{r.value}</div>
                    </div>
                  ))}
                </div>
              </Section>
            )}
            {intents.length > 0 && (
              <Section label={`Contributed to · ${intents.length}`} icon={Target}>
                <div className="flex flex-col gap-1.5">
                  {intents.map((o) => (
                    <div key={o.id} className="rounded-md bg-surface-high px-m py-1.5">
                      <div className="flex items-center gap-1.5"><Target size={12} className="shrink-0 text-primary/80" /><span data-type="body-s" className="truncate text-on-surface">{o.intent_name || o.intent_id}</span></div>
                      {o.takeaway && <div data-type="body-s" className="mt-0.5 text-on-surface-var">{o.takeaway}</div>}
                      {
}
                      {(o.fields?.length ?? 0) > 0 && (
                        <div data-type="caption" className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-m gap-y-0.5">
                          {o.fields!.map((f, i) => (
                            <div key={i} className="contents">
                              <span className="text-on-surface-low">{f.name}</span>
                              <OutcomeFieldValue field={f} />
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </Section>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export function OutcomeFieldValue({ field }: { field: IntentOutcomeField }) {
  const { type, value } = field
  if (value === null || value === undefined || value === '') return <span className="text-on-surface-low">—</span>
  if (type === 'boolean') return <span className="text-on-surface">{value ? 'Yes' : 'No'}</span>
  if (type === 'number') return <span className="text-on-surface tabular-nums">{String(value)}</span>
  if (type === 'url') return <a href={String(value)} target="_blank" rel="noreferrer" className="text-primary underline decoration-primary/40 break-all">{String(value)}</a>
  if (type === 'tags' && Array.isArray(value)) return <span className="flex flex-wrap gap-1">{value.map((t, i) => <span key={i} data-type="caption" className="rounded-pill bg-surface-high px-2 h-5 inline-flex items-center text-on-surface-var">{String(t)}</span>)}</span>
  return <span className="text-on-surface break-words">{String(value)}</span>
}

function FullscreenModal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const trapRef = useFocusTrap<HTMLDivElement>()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div ref={trapRef} className="fixed inset-0 z-[var(--z-content)] flex flex-col bg-surface/95 backdrop-blur-sm" onClick={onClose}>
      <div className="flex items-center gap-s border-b border-outline-variant/40 px-l py-3">
        <span data-type="title-m" className="flex-1 truncate text-on-surface" style={fvs(500)}>{title}</span>
        <button type="button" onClick={onClose} aria-label="Close fullscreen"
          className="grid size-8 place-items-center rounded-pill text-on-surface-low hover:bg-surface-high hover:text-on-surface"><X size={18} /></button>
      </div>
      <div className="flex-1 overflow-auto p-l" onClick={(e) => e.stopPropagation()}>
        <div className="mx-auto" style={{ maxWidth: 'var(--content-width, 56rem)' }}>{children}</div>
      </div>
    </div>
  )
}

function ExpandButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-label="Expand to full screen" title="Expand to full screen"
      className="grid size-6 place-items-center rounded text-on-surface-low hover:bg-surface-high hover:text-on-surface transition-colors">
      <Maximize2 size={13} />
    </button>
  )
}
