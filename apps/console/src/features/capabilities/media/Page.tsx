import { Select, TextInput } from '../../../shared/ui/forms'
import { BUSY_REASON, unavailableWhen } from '../../../shared/ui/unavailable'
import SpritePage from './SpritePage'
import AnimationPage from './AnimationPage'
import DownloadPage from './DownloadPage'
import EpisodePage from './EpisodePage'
import TimelinePage from './TimelinePage'
import VideoPage from './VideoPage'
import CleanupPage from './CleanupPage'
import DatasetsPage from './DatasetsPage'
import ImagePage from './ImagePage'
import Readiness from './Readiness'
import JobsPage from './JobsPage'
import { useEffect, useRef, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import LibraryPage from './LibraryPage'
import NativeMediaPage from './NativeMediaPage'
import { AreaNavigation } from '../AreaNavigation'
import { Clapperboard, Code2, Database, Download, Film, Gauge, Grid3X3, ImagePlus, Images, ListChecks, Paintbrush, Video, Wand2 } from 'lucide-react'

type Stroke = { tool: 'draw' | 'erase'; color: string; width: number; points: number[][] }
type Sketch = { id: string; width: number; height: number; strokes: Stroke[]; revision: number; source_artifact_id: string | null }
const base = '/api/capabilities/media/sketches'
async function request(path: string, method = 'GET', body?: unknown) {
  const response = await fetch(base + path, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const value = await response.json()
  if (!response.ok) throw new Error(value.error || 'Request failed')
  return value
}

function SketchPage({ sketchId, onSelectSketch, onJob }: { sketchId?: string; onSelectSketch?: (id: string) => void; onJob?: (id: string) => void } = {}) {
  const [items, setItems] = useState<Sketch[]>([])
  const [sketch, setSketch] = useState<Sketch | null>(null)
  const [strokes, setStrokes] = useState<Stroke[]>([])
  const [tool, setTool] = useState<'draw' | 'erase'>('draw')
  const [color, setColor] = useState('#ff0000')
  const [width, setWidth] = useState(8)
  const [source, setSource] = useState('')
  const [version, setVersion] = useState(1)
  const [size, setSize] = useState([640, 480])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [download, setDownload] = useState('')
  const canvas = useRef<HTMLCanvasElement>(null)
  const active = useRef<Stroke | null>(null)
  async function action(work: () => Promise<void>) {
    setBusy(true); setError('')
    try { await work() } catch (e) { setError(String((e as Error).message)) } finally { setBusy(false) }
  }
  function select(value: Sketch) { setSketch(value); setStrokes(value.strokes); setDownload('') }
  useEffect(() => {
    let live = true
    const load = async () => {
      try {
        const result = await request('')
        if (live) setItems(result.items)
        const id = sketchId ?? new URLSearchParams(location.hash.split('?')[1] || '').get('sketch')
        if (id) { const value = await request('/' + encodeURIComponent(id)); if (live) select(value) }
      } catch (e) { if (live) setError(String((e as Error).message)) }
    }
    void load(); if (!onSelectSketch) window.addEventListener('hashchange', load)
    return () => { live = false; if (!onSelectSketch) window.removeEventListener('hashchange', load) }
  }, [sketchId, onSelectSketch])
  useEffect(() => {
    const ctx = canvas.current?.getContext('2d')
    if (!ctx || !sketch) return
    ctx.clearRect(0, 0, sketch.width, sketch.height)
    for (const stroke of strokes) {
      ctx.globalCompositeOperation = stroke.tool === 'erase' ? 'destination-out' : 'source-over'
      ctx.strokeStyle = stroke.color; ctx.fillStyle = stroke.color; ctx.lineWidth = stroke.width; ctx.lineCap = 'round'; ctx.lineJoin = 'round'
      ctx.beginPath(); stroke.points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.stroke()
      for (const [x, y] of [stroke.points[0], stroke.points[stroke.points.length - 1]]) { ctx.beginPath(); ctx.arc(x, y, stroke.width / 2, 0, Math.PI * 2); ctx.fill() }
    }
  }, [strokes, sketch])
  function point(e: React.PointerEvent<HTMLCanvasElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    return [Math.max(0, Math.min(sketch!.width - 1, (e.clientX - rect.left) * sketch!.width / rect.width)), Math.max(0, Math.min(sketch!.height - 1, (e.clientY - rect.top) * sketch!.height / rect.height))]
  }
  const dirty = sketch && JSON.stringify(strokes) !== JSON.stringify(sketch.strokes)
  return <NativeMediaPage title="Image sketches">
    {error && <p role="alert">{error}</p>}
    <div className="grid gap-m rounded-lg bg-surface-container p-l sm:grid-cols-2 lg:grid-cols-5">
      <label>Source artifact ID (optional)<TextInput ariaLabel="Source artifact ID" value={String(source)} onChange={nextValue => setSource(nextValue)} /></label>
      <label>Source version<TextInput ariaLabel="Source version" type="number" min="1" value={String(version)} onChange={nextValue => setVersion(Number(nextValue))} /></label>
      {['Width', 'Height'].map((label, i) => <label key={label}>{label}<TextInput ariaLabel={label} type="number" min="1" max="4096" value={String(size[i])} onChange={nextValue => setSize(size.map((v, j) => j === i ? Number(nextValue) : v))} /></label>)}
      <Button disabled={busy || !!dirty} onClick={() => void action(async () => {
        const value = await request('', 'POST', { width: size[0], height: size[1], request_id: crypto.randomUUID(), ...(source ? { source_artifact_id: source, source_version: version } : {}) })
        select(value); setItems(old => [value, ...old]); if (onSelectSketch) onSelectSketch(value.id); else location.hash = '/capabilities/media?sketch=' + value.id
      })} disabledReason={busy ? BUSY_REASON : undefined}>New sketch</Button>
    </div>
    <label>Saved sketches<Select ariaLabel="Saved sketches" disabled={busy || !!dirty} value={String(sketch?.id || '')} onChange={nextValue => { if (nextValue) { if (onSelectSketch) onSelectSketch(nextValue); else location.hash = '/capabilities/media?sketch=' + nextValue } }} options={[{ value: String(""), label: "Choose a sketch" }, ...(items.map(item => ({ value: String(item.id), label: String(item.id) })) ?? [])]} /></label>
    {!sketch && <p>Create a blank canvas or open an image artifact at its original dimensions.</p>}
    {sketch && <>
      <div className="flex flex-wrap items-end gap-s rounded-lg bg-surface-container p-m">
        <Button ariaPressed={tool === 'draw'} onClick={() => setTool('draw')}>Draw</Button><Button ariaPressed={tool === 'erase'} onClick={() => setTool('erase')}>Erase</Button>
        <label>Color<input aria-label="Color" type="color" value={color} onChange={e => setColor(e.target.value)} /></label>
        <label>Brush width<TextInput ariaLabel="Brush width" type="number" min="1" max="128" value={String(width)} onChange={nextValue => setWidth(Math.max(1, Math.min(128, Number(nextValue))))} /></label>
        <Button disabled={!strokes.length || busy} onClick={() => setStrokes(old => old.slice(0, -1))} disabledReason={busy ? BUSY_REASON : undefined}>Undo</Button>
        <Button disabled={!dirty || busy} onClick={() => void action(async () => { select(await request('/' + sketch.id, 'PUT', { revision: sketch.revision, strokes })) })} disabledReason={busy ? BUSY_REASON : undefined}>Save</Button>
        <Button disabled={!dirty || busy} onClick={() => setStrokes(sketch.strokes)} disabledReason={busy ? BUSY_REASON : undefined}>Discard changes</Button>
        <Button disabled={!!dirty || busy} onClick={() => void action(async () => { const result = await request('/' + sketch.id + '/export', 'POST', { revision: sketch.revision }); setDownload('/api/artifacts/' + result.artifact_id + '/raw?version=' + result.version) })} disabledReason={busy ? BUSY_REASON : undefined}>Export PNG</Button>
        {download && <a href={download} download="sketch.png">Download PNG</a>}
      </div>
      <button {...unavailableWhen(!!dirty, 'Save or discard sketch changes before queuing an export', { busy })} onClick={() => { if (busy || dirty) return; setBusy(true); fetch('/api/capabilities/media/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation: 'sketch_export', sketch_id: sketch.id, revision: sketch.revision, request_id: crypto.randomUUID() }) }).then(async response => { const value = await response.json(); if (!response.ok) throw new Error(value.error); if (onJob) onJob(value.id); else location.hash = '#/capabilities/media?view=jobs' }).catch(reason => setError(String(reason))).finally(() => setBusy(false)) }}>Queue PNG export</button>
      <p role="status">Revision {sketch.revision}{dirty ? ' · Unsaved changes' : ' · Saved'}. Erase removes drawing only; the original image stays intact.</p>
      <div className="relative max-w-full overflow-hidden rounded-lg ring-1 ring-outline-variant/30" style={{ width: sketch.width, aspectRatio: `${sketch.width}/${sketch.height}`, background: 'white' }}>
        {sketch.source_artifact_id && <img alt="Original image" src={base + '/' + sketch.id + '/source'} className="absolute inset-0 w-full h-full" onError={() => setError('Original image is unavailable')} />}
        <canvas aria-label="Drawing canvas" ref={canvas} width={sketch.width} height={sketch.height} className="relative w-full h-full touch-none" onPointerDown={e => {
          if (busy || strokes.length >= 500) return
          e.currentTarget.setPointerCapture(e.pointerId); active.current = { tool, color, width, points: [point(e)] }; setStrokes(old => [...old, active.current!])
        }} onPointerMove={e => { if (active.current && active.current.points.length < 5000) { active.current = { ...active.current, points: [...active.current.points, point(e)] }; setStrokes(old => [...old.slice(0, -1), active.current!]) } }} onPointerUp={() => { active.current = null }} onPointerCancel={() => { active.current = null }} />
      </div>
    </>}
  </NativeMediaPage>
}

export default function Page({ view: selectedView, recordId, onNavigate }: { view?: string; recordId?: string; onNavigate?: (view: string, recordId?: string) => void } = {}) {
  const [consoleView, setView] = useState(() => typeof location === 'undefined' ? 'sketches' : new URLSearchParams(location.hash.split('?')[1] || '').get('view') || 'sketches')
  useEffect(() => { if (selectedView !== undefined) return; const update = () => setView(new URLSearchParams(location.hash.split('?')[1] || '').get('view') || 'sketches'); window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update) }, [selectedView])
  const view = selectedView ?? consoleView
  const go = (nextView: string, id?: string) => { if (onNavigate) onNavigate(nextView, id); else location.hash = nextView === 'sketches' ? '/capabilities/media' : `/capabilities/media?view=${nextView}${id ? '&' + ({ library: 'artifact', jobs: 'job', timelines: 'timeline', episodes: 'episode' } as Record<string, string>)[nextView] + '=' + encodeURIComponent(id) : ''}` }
  const content = view === 'downloads' ? <DownloadPage jobId={recordId} /> : view === 'animation' ? <AnimationPage jobId={recordId} /> : view === 'sprites' ? <SpritePage jobId={recordId} /> : view === 'episodes' ? <EpisodePage episodeId={recordId} /> : view === 'timelines' ? <TimelinePage timelineId={recordId} /> : view === 'videos' ? <VideoPage onJob={onNavigate ? id => go('jobs', id) : undefined} /> : view === 'cleanup' ? <CleanupPage onJob={onNavigate ? id => go('jobs', id) : undefined} /> : view === 'datasets' ? <DatasetsPage onJob={onNavigate ? id => go('jobs', id) : undefined} /> : view === 'images' ? <ImagePage onJob={onNavigate ? id => go('jobs', id) : undefined} /> : view === 'readiness' ? <Readiness showSettingsLinks={!onNavigate} /> : view === 'jobs' ? <JobsPage selectedJobId={recordId} /> : view === 'library' ? onNavigate ? <LibraryPage artifactId={recordId || ''} onSelectArtifact={id => go('library', id || undefined)} onNavigate={() => go('sketches')} /> : <LibraryPage /> : <SketchPage sketchId={recordId} onSelectSketch={onNavigate ? id => go('sketches', id) : undefined} onJob={onNavigate ? id => go('jobs', id) : undefined} />
  const destinations = [
    { id: 'sketches', label: 'Image sketches', icon: Paintbrush, group: 'Create' },
    { id: 'images', label: 'Generate image', icon: ImagePlus, group: 'Create' },
    { id: 'videos', label: 'Generate video', icon: Video, group: 'Create' },
    { id: 'animation', label: 'Code animation', icon: Code2, group: 'Create' },
    { id: 'sprites', label: 'Sprite production', icon: Grid3X3, group: 'Create' },
    { id: 'episodes', label: 'Continuous episodes', icon: Clapperboard, group: 'Edit' },
    { id: 'timelines', label: 'Video timeline', icon: Film, group: 'Edit' },
    { id: 'cleanup', label: 'Image cleanup', icon: Wand2, group: 'Edit' },
    { id: 'library', label: 'Media library', icon: Images, group: 'Manage' },
    { id: 'jobs', label: 'Media jobs', icon: ListChecks, group: 'Manage' },
    { id: 'downloads', label: 'Source downloader', icon: Download, group: 'Manage' },
    { id: 'datasets', label: 'Training datasets', icon: Database, group: 'Manage' },
    { id: 'readiness', label: 'Media readiness', icon: Gauge, group: 'Manage' },
  ]
  return <div className="h-full" onClickCapture={event => {
    if (!onNavigate) return
    const link = (event.target as HTMLElement).closest('a[href^="#/capabilities/media"]')
    if (!link) return
    const url = new URL(link.getAttribute('href') || '', location.href)
    const query = new URLSearchParams(url.hash.split('?')[1] || '')
    const target = query.get('view') || 'sketches'
    event.preventDefault()
    go(target, query.get(({ sketches: 'sketch', library: 'artifact', jobs: 'job', timelines: 'timeline', episodes: 'episode', sprites: 'job', animation: 'job', downloads: 'job' } as Record<string, string>)[target]) || undefined)
  }}><AreaNavigation label="Media workspaces" items={destinations} active={view} onChange={id => go(id)}>{content}</AreaNavigation></div>
}
