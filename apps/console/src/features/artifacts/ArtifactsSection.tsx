import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, FolderInput, MessagesSquare } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { HeaderActions } from '../../shared/ui/HeaderActions'
import { Loading, LoadError } from '../../shared/ui/ListScaffold'
import { SearchField } from '../../shared/ui/SearchField'
import { ResultAnnouncement } from '../../shared/ui/ListControls'
import { Segmented } from '../../shared/ui/forms'
import { QuietButton } from '../../shared/ui/QuietButton'
import { FilterMenu, type FilterSectionDef } from '../../shared/ui/FilterMenu'
import { api, type Artifact } from '../../shared/data/api'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { newSessionTarget } from '../../shared/ui/content/commentTarget'
import { notify } from '../../app/shell/appSdk'
import { promptInput } from '../../shared/ui/dialog'
import { ARTIFACT_KINDS } from '../files/fileMeta'
import { Morph } from '../../shared/ui/motion'
import { ArtifactGrid } from './ArtifactGrid'
import { ArtifactViewer } from './ArtifactViewer'
import { ArtifactIteratePanel, ITERATE_PENDING } from './ArtifactIteratePanel'
import { DeployedAppsMenu } from './ArtifactDeploy'
import { PageTitle } from '../../shared/ui/PageTitle'

const SOURCES = ['chat', 'cron', 'subagent', 'manual', 'import'] as const
const SORTS = [
  { key: 'updated', label: 'Recent' },
  { key: 'name', label: 'Name' },
  { key: 'kind', label: 'Kind' },
] as const

export function ArtifactsSection({ sub, navigate, query: routeQuery, setQuery }: RouteProps) {
  const slug = (sub || '').split('/')[0] || ''

  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [loading, setLoading] = useState(false)
  const [loadErr, setLoadErr] = useState<unknown>(null)

  const [q, setQ] = useQueryParam(routeQuery, setQuery, 'q', '', { replace: true })
  const [kind, setKind] = useQueryParam(routeQuery, setQuery, 'kind', '', { replace: true })
  const [src, setSrc] = useQueryParam(routeQuery, setQuery, 'src', '', { replace: true })
  const [col, setCol] = useQueryParam(routeQuery, setQuery, 'col', '', { replace: true })
  const [sort, setSort] = useQueryParam(routeQuery, setQuery, 'sort', 'updated', { replace: true })
  const [vParam, setVParam] = useQueryParam(routeQuery, setQuery, 'v', '', { replace: true })
  const [iterate, setIterate] = useQueryParam(routeQuery, setQuery, 'iterate', '', { replace: true })

  const load = useCallback(async () => {
    setLoading(true)
    try { setArtifacts(await api.artifacts(q.trim() ? { q: q.trim() } : undefined)); setLoadErr(null) }
    catch (e) { setLoadErr(e) }
    finally { setLoading(false) }
  }, [q])
  useEffect(() => {
    const timer = window.setTimeout(load, q.trim() ? 200 : 0)
    return () => window.clearTimeout(timer)
  }, [load, q])

  const collections = useMemo(() => {
    const s = new Set<string>()
    for (const a of artifacts) if (a.collection) s.add(a.collection)
    return [...s].sort()
  }, [artifacts])

  const filterSections = useMemo<FilterSectionDef[]>(() => {
    const bySource = new Map<string, number>()
    const byCollection = new Map<string, number>()
    for (const a of artifacts) {
      if (a.source) bySource.set(a.source, (bySource.get(a.source) ?? 0) + 1)
      if (a.collection) byCollection.set(a.collection, (byCollection.get(a.collection) ?? 0) + 1)
    }
    const list: FilterSectionDef[] = [{
      title: 'Source', value: src, defaultKey: '', onChange: setSrc,
      options: [
        { key: '', label: 'Any source', count: artifacts.length },
        ...SOURCES.map((k) => ({ key: k, label: k[0].toUpperCase() + k.slice(1), count: bySource.get(k) })),
      ],
    }]
    if (collections.length > 0) list.push({
      title: 'Collection', value: col, defaultKey: '', onChange: setCol,
      options: [
        { key: '', label: 'All collections', count: artifacts.length },
        ...collections.map((c) => ({ key: c, label: c, count: byCollection.get(c) })),
      ],
    })
    list.push({
      title: 'Sort by', value: sort, defaultKey: 'updated', onChange: setSort,
      options: SORTS.map((o) => ({ key: o.key, label: o.label })),
    })
    return list
  }, [artifacts, collections, src, col, sort, setSrc, setCol, setSort])

  const filtered = useMemo(() => {
    let out = artifacts.filter((a) => {
      if (kind && a.kind !== kind) return false
      if (src && a.source !== src) return false
      if (col && a.collection !== col) return false
      return true
    })
    if (sort === 'name') out = [...out].sort((a, b) => a.name.localeCompare(b.name))
    else if (sort === 'kind') out = [...out].sort((a, b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name))
    return out
  }, [artifacts, q, kind, src, col, sort])

  const open = (a: Artifact) => navigate(`artifacts/${a.slug}`)
  const back = () => { setVParam(''); setIterate(''); navigate('artifacts') }

  const openSourceFile = useCallback((path: string) => {
    const dir = path.replace(/\/[^/]*$/, '')
    navigate(`files?dir=${encodeURIComponent(dir)}&file=${encodeURIComponent(path)}`)
  }, [navigate])

  const active = slug ? artifacts.find((a) => a.slug === slug) : undefined
  const assignCollection = async () => {
    if (!slug) return
    const name = await promptInput({
      title: 'Set collection', label: 'Collection',
      placeholder: collections[0] ?? 'e.g. Dashboards',
      initial: active?.collection ?? '',
      confirmLabel: 'Save',
    })
    if (name === null || name === undefined) return
    try {
      await api.updateArtifact(slug, { collection: name.trim() })
      await load()
    } catch (e) { notify(`Couldn't set collection: ${String((e as Error)?.message || e)}`, 'error') }
  }

  const initialVersion = vParam ? Number(vParam) || undefined : undefined
  const canFile = !!slug && !!active && !active.readonly
  const canIterate = canFile
  const iterateOpen = canIterate && !!iterate

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={<div className="flex min-w-0 items-center gap-m">
          {slug
            ? <QuietButton onClick={back} title="Back to the library"><ArrowLeft size={13} /> Library</QuietButton>
            : <PageTitle className="shrink-0">Artifacts</PageTitle>}
          {slug && active && <span className="truncate text-on-surface" data-type="title-l">{active.name}</span>}
        </div>}
        right={canFile ? (
          <HeaderActions>
            <QuietButton onClick={() => setIterate(iterate ? '' : ITERATE_PENDING)} ariaExpanded={iterateOpen}
              title={iterateOpen ? 'Close the iterate panel' : 'Open a chat beside this artifact and change it by asking'}>
              <MessagesSquare size={13} /> {iterateOpen ? 'Close iterate' : 'Iterate with agent'}
            </QuietButton>
            <QuietButton onClick={assignCollection} title="Group this artifact under a library collection">
              <FolderInput size={13} /> {active.collection || 'Set collection'}
            </QuietButton>
          </HeaderActions>
        ) : undefined}
      />

      {slug ? (
        <div className="mx-auto flex min-h-0 w-full flex-1 flex-col lg:flex-row"
          style={{ maxWidth: iterateOpen ? undefined : 'var(--content-width)' }}>
          {
}
          <Morph id={`artifact-${slug}`} className="flex min-h-0 min-w-0 flex-1 flex-col">
            <ArtifactViewer key={slug} slug={slug} onChanged={load}
              onDeleted={() => { back(); load() }} onOpenSourceFile={openSourceFile}
              initialVersion={initialVersion}
              onVersionChange={(v) => setVParam(v === null ? '' : String(v))}
              defaultDetailsOpen={initialVersion != null}
              commentTarget={navigate ? newSessionTarget(navigate, { name: `Comments: ${active?.name ?? slug}` }) : undefined} />
          </Morph>
          {iterateOpen && (
            <div className="flex min-h-0 shrink-0 basis-1/2 flex-col lg:w-[26rem] lg:basis-auto xl:w-[30rem]">
              <ArtifactIteratePanel key={slug} slug={slug} name={active?.name ?? slug}
                session={iterate} onSession={setIterate} onClose={() => setIterate('')} />
            </div>
          )}
        </div>
      ) : (
        <div className="mx-auto flex min-h-0 w-full flex-1 flex-col" style={{ maxWidth: 'var(--content-width)' }}>
          { }
          <div className="flex flex-wrap items-center gap-m border-b border-outline/40 px-l py-2.5">
            <div className="w-64"><SearchField size="sm" value={q} onChange={setQ} placeholder="Search artifacts…" ariaLabel="Search artifacts" name="artifacts-search" /></div>
            {
}
            <Segmented ariaLabel="Artifact kind" value={kind} onChange={setKind} collapse="scroll"
              options={[{ key: '', label: 'All kinds' }, ...ARTIFACT_KINDS.map((k) => ({ key: k.key, label: k.label }))]} />
            <FilterMenu sections={filterSections} />
            {
}
            <div className="ml-auto"><DeployedAppsMenu onOpen={(s) => navigate(`artifacts/${s}`)} onChanged={load} /></div>
            {
}
            <ResultAnnouncement count={filtered.length} noun="artifacts" active={!!(q.trim() || kind || src || col)} />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {loadErr && artifacts.length === 0
              ? <LoadError what="artifacts" error={loadErr} onRetry={load} />
              : loading && artifacts.length === 0
              ? <Loading what="artifacts" />
              : <ArtifactGrid artifacts={filtered} onOpen={open}
                  onBrowseFiles={() => navigate('files')}
                  narrowed={!!(q.trim() || kind || src || col)} />}
          </div>
        </div>
      )}
    </div>
  )
}
