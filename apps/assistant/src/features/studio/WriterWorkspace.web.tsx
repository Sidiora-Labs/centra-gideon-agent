import { useEffect, useState } from 'react'
import Page from '../../../../console/src/features/capabilities/creative/Page'
import { requestJson } from '../../../../console/src/shared/data/gatewayRequest'
import { studioRecordRef, type StudioModuleProps } from './studioContracts'
import { studioDestination, studioTarget, createStudioRoute } from './studioRoutes'

type Ref = { id: string; revision: number }
type Work = { id: string; title: string; revision: number; active_draft_id: string | null; author_ref: Ref | null; universe_ref: Ref | null }
type Preview = { workId: string; revision: number; draftId: string; title: string; text: string }
const workRoot = '/api/capabilities/creative/works'
const views = new Set(['ingredients', 'boards', 'universes', 'authors', 'works', 'stories', 'series', 'production', 'direction', 'commissions', 'exports'])

export default function WriterWorkspace({ route, scope, navigate }: StudioModuleProps) {
  const target = studioTarget(route)
  const initialView = target?.area === 'creative' && views.has(target.view) ? target.view : 'works'
  const initialWork = route.record?.kind === 'creative.work' ? route.record.id : ''
  const [view, setView] = useState(initialView)
  const [workId, setWorkId] = useState(initialWork)
  const [work, setWork] = useState<Work | null>(null)
  const [versions, setVersions] = useState<Work[]>([])
  const [revision, setRevision] = useState<number | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [seriesId, setSeriesId] = useState(route.record?.kind === 'creative.series' ? route.record.id : '')
  const [ingredientId, setIngredientId] = useState(route.record?.kind === 'creative.ingredient' ? route.record.id : '')
  const [boardId, setBoardId] = useState('')
  const [authorId, setAuthorId] = useState('')
  const [universeId, setUniverseId] = useState('')

  useEffect(() => { setView(initialView) }, [route.placement?.id])
  useEffect(() => { if (initialWork) setWorkId(initialWork) }, [initialWork])
  useEffect(() => {
    if (work && scope.cacheKey && work.id === workId
      && (route.record?.id !== work.id || route.placement?.query?.revision !== String(work.revision))) {
      navigate(createStudioRoute('capabilities/creative/works', route.returnTo,
        studioRecordRef(scope, { kind: 'creative.work', id: work.id, revision: work.revision }), scope))
    }
  }, [work?.id, work?.revision, workId, route.record?.id, route.placement?.query?.revision])
  useEffect(() => {
    let current = true
    setVersions([])
    setPreview(null)
    setError('')
    if (!workId) { setRevision(null); return }
    requestJson<{ items: Work[] }>(`${workRoot}/${encodeURIComponent(workId)}/revisions`)
      .then(result => { if (current) { setVersions(result.items); setRevision(previous => previous && result.items.some(item => item.revision === previous) ? previous : result.items[0]?.revision ?? null) } })
      .catch(reason => { if (current) setError(reason instanceof Error ? reason.message : 'Unable to load work revisions') })
    return () => { current = false }
  }, [workId, work?.revision, retry])

  function selectWork(id: string) {
    setWorkId(id)
    setWork(null)
    setPreview(null)
    if (id && scope.cacheKey) navigate(createStudioRoute('capabilities/creative/works', route.returnTo,
      studioRecordRef(scope, { kind: 'creative.work', id }), scope))
  }

  function loaded(record: Work | null) {
    setWork(record)
    if (record) {
      setAuthorId(record.author_ref?.id || '')
      setUniverseId(record.universe_ref?.id || '')
      setRevision(record.revision)
      if (preview && (preview.workId !== record.id || preview.revision !== record.revision)) setPreview(null)
    }
  }

  async function previewRevision() {
    if (!workId || !revision) return
    setPreviewing(true); setError(''); setPreview(null)
    try {
      const selected = await requestJson<Work>(`${workRoot}/${encodeURIComponent(workId)}/export?revision=${revision}`)
      if (!selected.active_draft_id) throw new Error('This revision has no saved manuscript draft. Save a draft before preview or export.')
      const draft = await requestJson<{ missing?: boolean; text?: string }>(`${workRoot}/${encodeURIComponent(workId)}/drafts/${encodeURIComponent(selected.active_draft_id)}`)
      if (draft.missing || !draft.text) throw new Error('The selected manuscript artifact is unavailable.')
      setPreview({ workId, revision: selected.revision, draftId: selected.active_draft_id, title: selected.title, text: draft.text })
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to preview manuscript') }
    finally { setPreviewing(false) }
  }

  const selectedVersion = versions.find(item => item.revision === revision)
  const canExport = !!preview && preview.workId === workId && preview.revision === revision && preview.draftId === selectedVersion?.active_draft_id
  const destination = studioDestination(route)
  return <div className="min-w-0 space-y-m text-on-surface" aria-label="Writer workspace">
    <div className="flex flex-wrap items-center justify-between gap-m rounded-lg bg-surface-container p-m">
      <div><h2 data-type="title-m">{work?.title || destination?.label || 'Writer'}</h2><p data-type="body-s" className="text-on-surface-low">{work ? `Work ${work.id} · current revision ${work.revision}` : 'Choose or create a writing work.'}</p></div>
      <div className="flex flex-wrap items-end gap-s"><label className="text-sm">Selected revision
        <select aria-label="Selected work revision" className="ml-s rounded-md border border-outline-variant/30 bg-surface-container px-s py-xs" value={revision ?? ''}
          disabled={!versions.length} onChange={event => { setRevision(Number(event.target.value)); setPreview(null) }}>
          {!versions.length && <option value="">No revision</option>}{versions.map(item => <option value={item.revision} key={item.revision}>Revision {item.revision}{item.active_draft_id ? ' · manuscript' : ' · no draft'}</option>)}
        </select></label>
        <button type="button" className="rounded-md bg-primary px-m py-s text-on-primary disabled:opacity-50" disabled={!revision || previewing} onClick={() => void previewRevision()}>{previewing ? 'Loading preview…' : 'Preview selected'}</button>
        <button type="button" className="rounded-md border border-outline-variant/30 px-m py-s disabled:opacity-50" disabled={!canExport} onClick={() => setView('exports')}>Export selected</button>
      </div>
    </div>
    {error && <p role="alert" className="rounded-md border border-danger/40 p-m text-danger">{error} <button type="button" className="underline" onClick={() => { setRetry(value => value + 1); if (revision) void previewRevision() }}>Retry</button></p>}
    {preview && <section aria-label="Manuscript preview" className="space-y-s rounded-lg bg-surface-container p-l"><div className="flex flex-wrap items-baseline justify-between gap-s"><h3 data-type="title-m">{preview.title}</h3><p data-type="caption">Revision {preview.revision} · draft {preview.draftId}</p></div><article className="max-h-[40rem] overflow-auto whitespace-pre-wrap rounded-md bg-surface p-l font-serif leading-7">{preview.text}</article></section>}
    {view === 'exports' && !canExport ? <section role="status" className="rounded-lg bg-surface-container p-l">Choose and preview a saved manuscript revision before exporting it.</section> :
      <Page activeView={view} onViewChange={next => setView(next)} workId={workId} workRevision={revision ?? undefined}
        onSelectWork={selectWork} onWorkLoaded={loaded} draftStorageKey={`gideon-writer:${scope.cacheKey}`}
        seriesId={seriesId} onSelectSeries={setSeriesId} ingredientId={ingredientId} onSelectIngredient={setIngredientId}
        boardId={boardId} onSelectBoard={setBoardId} authorId={authorId} onSelectAuthor={setAuthorId}
        universeId={universeId} onSelectUniverse={setUniverseId} />}
  </div>
}
