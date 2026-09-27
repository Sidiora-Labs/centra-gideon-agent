import { useEffect, useRef, useState } from 'react'
import Page from '../../../../console/src/features/capabilities/creative/Page'
import { requestJson } from '../../../../console/src/shared/data/gatewayRequest'
import { studioRecordRef, type StudioModuleProps } from './studioContracts'
import { studioDestination, studioTarget, createStudioRoute } from './studioRoutes'

type Ref = { id: string; revision: number }
type Work = { id: string; title: string; revision: number; active_draft_id: string | null; author_ref: Ref | null; universe_ref: Ref | null }
type Preview = { workId: string; revision: number; draftId: string; title: string; text: string }
const workRoot = '/api/capabilities/creative/works'
const views = new Set(['ingredients', 'boards', 'universes', 'authors', 'works', 'stories', 'series', 'production', 'direction', 'commissions', 'exports'])
const recordKinds: Record<string, string> = {
  ingredients: 'creative.ingredient', boards: 'creative.board', universes: 'creative.universe',
  authors: 'creative.author', works: 'creative.work', series: 'creative.series',
  production: 'creative.series', direction: 'creative.work', commissions: 'creative.work', exports: 'creative.work',
}

export default function WriterWorkspace({ route, scope, navigate }: StudioModuleProps) {
  const target = studioTarget(route)
  const initialView = target?.area === 'creative' && views.has(target.view) ? target.view : 'works'
  const initialWork = route.record?.kind === 'creative.work' ? route.record.id : route.placement?.query?.contextWorkId ?? ''
  const view = initialView
  const [workId, setWorkId] = useState(initialWork)
  const [work, setWork] = useState<Work | null>(null)
  const [versions, setVersions] = useState<Work[]>([])
  const routeRevisionValue = route.record?.kind === 'creative.work'
    ? route.placement?.query?.revision : route.placement?.query?.contextRevision
  const routeRevision = routeRevisionValue ? Number(routeRevisionValue) : null
  const [revision, setRevision] = useState<number | null>(routeRevision)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [seriesId, setSeriesId] = useState(route.record?.kind === 'creative.series' ? route.record.id : '')
  const [ingredientId, setIngredientId] = useState(route.record?.kind === 'creative.ingredient' ? route.record.id : '')
  const [boardId, setBoardId] = useState(route.record?.kind === 'creative.board' ? route.record.id : '')
  const [authorId, setAuthorId] = useState(route.record?.kind === 'creative.author' ? route.record.id : '')
  const [universeId, setUniverseId] = useState(route.record?.kind === 'creative.universe' ? route.record.id : '')
  const previewGeneration = useRef(0)
  const selectionKey = `${scope.cacheKey}\0${workId}\0${revision ?? ''}\0${routeRevision ?? ''}\0${route.record?.id ?? ''}\0${route.placement?.query?.contextWorkId ?? ''}`
  const selection = useRef(selectionKey)
  if (selection.current !== selectionKey) { selection.current = selectionKey; previewGeneration.current++ }

  useEffect(() => { if (route.record?.kind === 'creative.work') setWorkId(route.record.id) }, [route.record?.kind, route.record?.id])
  useEffect(() => { if (route.record?.kind !== 'creative.work' && route.placement?.query?.contextWorkId) setWorkId(route.placement.query.contextWorkId) }, [route.record?.kind, route.placement?.query?.contextWorkId])
  useEffect(() => { if (route.record?.kind === 'creative.series') setSeriesId(route.record.id) }, [route.record?.kind, route.record?.id])
  useEffect(() => { if (route.record?.kind === 'creative.ingredient') setIngredientId(route.record.id) }, [route.record?.kind, route.record?.id])
  useEffect(() => { if (route.record?.kind === 'creative.board') setBoardId(route.record.id) }, [route.record?.kind, route.record?.id])
  useEffect(() => { if (route.record?.kind === 'creative.author') setAuthorId(route.record.id) }, [route.record?.kind, route.record?.id])
  useEffect(() => { if (route.record?.kind === 'creative.universe') setUniverseId(route.record.id) }, [route.record?.kind, route.record?.id])
  useEffect(() => { if (routeRevision) { setRevision(routeRevision); setPreview(null) } }, [routeRevision, route.record?.id])
  useEffect(() => {
    if (work && work.id === workId && route.record?.kind === 'creative.work' && route.record.id === workId
      && !routeRevision && scope.cacheKey) {
      navigate(createStudioRoute(`capabilities/creative/${view}`, route.returnTo,
        studioRecordRef(scope, { kind: 'creative.work', id: workId, revision: work.revision }), scope))
    }
  }, [work?.id, work?.revision, workId, route.record?.kind, route.record?.id, routeRevision, view])
  useEffect(() => {
    let current = true
    setVersions([])
    setPreview(null)
    setError('')
    if (!workId) { setRevision(null); return }
    requestJson<{ items: Work[] }>(`${workRoot}/${encodeURIComponent(workId)}/revisions`)
      .then(result => { if (current) { setVersions(result.items); setRevision(previous => routeRevision && result.items.some(item => item.revision === routeRevision) ? routeRevision : previous && result.items.some(item => item.revision === previous) ? previous : result.items[0]?.revision ?? null) } })
      .catch(reason => { if (current) setError(reason instanceof Error ? reason.message : 'Unable to load work revisions') })
    return () => { current = false }
  }, [workId, retry, routeRevision])

  function move(next: string, id = '') {
    const kind = recordKinds[next]
    const ref = kind && id ? studioRecordRef(scope, { kind, id,
      ...(kind === 'creative.work' && revision && id === workId ? { revision } : {}) }) : undefined
    const nextRoute = createStudioRoute(`capabilities/creative/${next}`, route.returnTo, ref, scope)
    if (kind !== 'creative.work' && workId) {
      nextRoute.placement = { ...nextRoute.placement!, query: { ...nextRoute.placement?.query,
        contextWorkId: workId, ...(revision ? { contextRevision: String(revision) } : {}) } }
    }
    navigate(nextRoute)
  }

  function changeView(next: string) {
    const id = next === 'works' || next === 'exports' || next === 'direction' || next === 'commissions' ? workId
      : next === 'series' || next === 'production' ? seriesId
        : next === 'ingredients' ? ingredientId : next === 'boards' ? boardId
          : next === 'authors' ? authorId : next === 'universes' ? universeId : ''
    move(next, id)
  }

  function selectWork(id: string) {
    if (id !== workId) {
      setWorkId(id); setWork(null); setPreview(null); setRevision(null); setPreviewing(false)
      previewGeneration.current++
    }
    if (route.record?.kind !== 'creative.work' || route.record.id !== id || view !== 'works') move('works', id)
  }

  function loaded(record: Work | null) {
    setWork(record)
    if (record) {
      setAuthorId(record.author_ref?.id || '')
      setUniverseId(record.universe_ref?.id || '')
      if (!routeRevision || (work?.id === record.id && routeRevision === work.revision && record.revision !== work.revision)) {
        setRevision(record.revision)
        if (route.record?.kind === 'creative.work' && route.record.id === record.id) {
          navigate(createStudioRoute(`capabilities/creative/${view}`, route.returnTo,
            studioRecordRef(scope, { kind: 'creative.work', id: record.id, revision: record.revision }), scope))
        }
      }
      if (work?.revision !== record.revision) {
        previewGeneration.current++; setPreview(null); setPreviewing(false)
        if (work?.id === record.id) setRetry(value => value + 1)
      }
    }
  }

  async function previewRevision() {
    if (!workId || !revision) return
    const requestedWork = workId, requestedRevision = revision
    const requestedSelection = selection.current, generation = ++previewGeneration.current
    const current = () => generation === previewGeneration.current && requestedSelection === selection.current
    setPreviewing(true); setError(''); setPreview(null)
    try {
      const selected = await requestJson<Work>(`${workRoot}/${encodeURIComponent(requestedWork)}/export?revision=${requestedRevision}`)
      if (!current()) return
      if (!selected.active_draft_id) throw new Error('This revision has no saved manuscript draft. Save a draft before preview or export.')
      const draft = await requestJson<{ missing?: boolean; text?: string }>(`${workRoot}/${encodeURIComponent(requestedWork)}/drafts/${encodeURIComponent(selected.active_draft_id)}`)
      if (!current()) return
      if (draft.missing || !draft.text) throw new Error('The selected manuscript artifact is unavailable.')
      setPreview({ workId: requestedWork, revision: selected.revision, draftId: selected.active_draft_id, title: selected.title, text: draft.text })
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : 'Unable to preview manuscript') }
    finally { if (current()) setPreviewing(false) }
  }

  useEffect(() => {
    if (view === 'exports' && workId && revision && !preview && !previewing && !error
      && versions.some(item => item.revision === revision && item.active_draft_id)) void previewRevision()
  }, [view, workId, revision, versions])

  const selectedVersion = versions.find(item => item.revision === revision)
  const canExport = !!preview && preview.workId === workId && preview.revision === revision && preview.draftId === selectedVersion?.active_draft_id
  const destination = studioDestination(route)
  return <div className="min-w-0 space-y-m text-on-surface" aria-label="Writer workspace">
    <div className="flex flex-wrap items-center justify-between gap-m rounded-lg bg-surface-container p-m">
      <div><h2 data-type="title-m">{work?.title || destination?.label || 'Writer'}</h2><p data-type="body-s" className="text-on-surface-low">{work ? `Work ${work.id} · current revision ${work.revision}` : 'Choose or create a writing work.'}</p></div>
      <div className="flex flex-wrap items-end gap-s"><label className="text-sm">Selected revision
        <select aria-label="Selected work revision" className="ml-s rounded-md border border-outline-variant/30 bg-surface-container px-s py-xs" value={revision ?? ''}
          disabled={!versions.length} onChange={event => {
            const next = Number(event.target.value)
            if (!Number.isSafeInteger(next) || next < 1 || !versions.some(item => item.revision === next) || !workId) return
            setRevision(next); setPreview(null); setPreviewing(false); previewGeneration.current++
            navigate(createStudioRoute('capabilities/creative/works', route.returnTo,
              studioRecordRef(scope, { kind: 'creative.work', id: workId, revision: next }), scope))
          }}>
          {!versions.length && <option value="">No revision</option>}{versions.map(item => <option value={item.revision} key={item.revision}>Revision {item.revision}{item.active_draft_id ? ' · manuscript' : ' · no draft'}</option>)}
        </select></label>
        <button type="button" className="rounded-md bg-primary px-m py-s text-on-primary disabled:opacity-50" disabled={!revision || previewing} onClick={() => void previewRevision()}>{previewing ? 'Loading preview…' : 'Preview selected'}</button>
        <button type="button" className="rounded-md border border-outline-variant/30 px-m py-s disabled:opacity-50" disabled={!canExport} onClick={() => changeView('exports')}>Export selected</button>
      </div>
    </div>
    {error && <p role="alert" className="rounded-md border border-danger/40 p-m text-danger">{error} <button type="button" className="underline" onClick={() => { setRetry(value => value + 1); if (revision) void previewRevision() }}>Retry</button></p>}
    {canExport && preview && <section aria-label="Manuscript preview" className="space-y-s rounded-lg bg-surface-container p-l"><div className="flex flex-wrap items-baseline justify-between gap-s"><h3 data-type="title-m">{preview.title}</h3><p data-type="caption">Revision {preview.revision} · draft {preview.draftId}</p></div><article className="max-h-[40rem] overflow-auto whitespace-pre-wrap rounded-md bg-surface p-l font-serif leading-7">{preview.text}</article></section>}
    {view === 'exports' && !canExport ? <section role="status" className="rounded-lg bg-surface-container p-l">Choose and preview a saved manuscript revision before exporting it.</section> :
      <Page activeView={view} onViewChange={changeView} workId={workId} workRevision={revision ?? undefined}
        onSelectWork={selectWork} onWorkLoaded={loaded} draftStorageKey={`gideon-writer:${scope.cacheKey}`}
        seriesId={seriesId} onSelectSeries={id => { setSeriesId(id); move('series', id) }} ingredientId={ingredientId} onSelectIngredient={id => { setIngredientId(id); move('ingredients', id) }}
        boardId={boardId} onSelectBoard={id => { setBoardId(id); move('boards', id) }} authorId={authorId} onSelectAuthor={id => { setAuthorId(id); move('authors', id) }}
        universeId={universeId} onSelectUniverse={id => { setUniverseId(id); move('universes', id) }} />}
  </div>
}
