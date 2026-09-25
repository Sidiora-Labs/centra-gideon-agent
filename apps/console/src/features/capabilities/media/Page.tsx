import VideoPage from './VideoPage'
import CleanupPage from './CleanupPage'
import DatasetsPage from './DatasetsPage'
import ImagePage from './ImagePage'
import Readiness from './Readiness'
import JobsPage from './JobsPage'
import { useEffect, useRef, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import LibraryPage from './LibraryPage'

type Stroke = { tool: 'draw' | 'erase'; color: string; width: number; points: number[][] }
type Sketch = { id: string; width: number; height: number; strokes: Stroke[]; revision: number; source_artifact_id: string | null }
const base = '/api/capabilities/media/sketches'
async function request(path: string, method = 'GET', body?: unknown) {
  const response = await fetch(base + path, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const value = await response.json()
  if (!response.ok) throw new Error(value.error || 'Request failed')
  return value
}

function SketchPage() {
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
        const id = new URLSearchParams(location.hash.split('?')[1] || '').get('sketch')
        if (id) { const value = await request('/' + encodeURIComponent(id)); if (live) select(value) }
      } catch (e) { if (live) setError(String((e as Error).message)) }
    }
    void load(); window.addEventListener('hashchange', load)
    return () => { live = false; window.removeEventListener('hashchange', load) }
  }, [])
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
  return <section className="p-4 space-y-4 overflow-auto" aria-label="Image sketches">
    <h1>Image sketches</h1>
    <a href="#/capabilities/media?view=library">Media library</a>
<a href="#/capabilities/media?view=jobs">Media jobs</a><a href="#/capabilities/media?view=readiness">Media readiness</a><a href="#/capabilities/media?view=images">Generate image</a><a href="#/capabilities/media?view=datasets">Training datasets</a><a href="#/capabilities/media?view=videos">Generate video</a><a href="#/capabilities/media?view=cleanup">Image cleanup</a>
    {error && <p role="alert">{error}</p>}
    <div className="flex flex-wrap gap-3">
      <label>Source artifact ID (optional)<input aria-label="Source artifact ID" value={source} onChange={e => setSource(e.target.value)} /></label>
      <label>Source version<input aria-label="Source version" type="number" min="1" value={version} onChange={e => setVersion(Number(e.target.value))} /></label>
      {['Width', 'Height'].map((label, i) => <label key={label}>{label}<input aria-label={label} type="number" min="1" max="4096" value={size[i]} onChange={e => setSize(size.map((v, j) => j === i ? Number(e.target.value) : v))} /></label>)}
      <Button disabled={busy || !!dirty} onClick={() => void action(async () => {
        const value = await request('', 'POST', { width: size[0], height: size[1], request_id: crypto.randomUUID(), ...(source ? { source_artifact_id: source, source_version: version } : {}) })
        select(value); setItems(old => [value, ...old]); location.hash = '/capabilities/media?sketch=' + value.id
      })}>New sketch</Button>
    </div>
    <label>Saved sketches<select aria-label="Saved sketches" disabled={busy || !!dirty} value={sketch?.id || ''} onChange={e => { if (e.target.value) location.hash = '/capabilities/media?sketch=' + e.target.value }}>
      <option value="">Choose a sketch</option>{items.map(item => <option key={item.id} value={item.id}>{item.id}</option>)}
    </select></label>
    {!sketch && <p>Create a blank canvas or open an image artifact at its original dimensions.</p>}
    {sketch && <>
      <div className="flex flex-wrap gap-3">
        <Button aria-pressed={tool === 'draw'} onClick={() => setTool('draw')}>Draw</Button><Button aria-pressed={tool === 'erase'} onClick={() => setTool('erase')}>Erase</Button>
        <label>Color<input aria-label="Color" type="color" value={color} onChange={e => setColor(e.target.value)} /></label>
        <label>Brush width<input aria-label="Brush width" type="number" min="1" max="128" value={width} onChange={e => setWidth(Math.max(1, Math.min(128, Number(e.target.value))))} /></label>
        <Button disabled={!strokes.length || busy} onClick={() => setStrokes(old => old.slice(0, -1))}>Undo</Button>
        <Button disabled={!dirty || busy} onClick={() => void action(async () => { select(await request('/' + sketch.id, 'PUT', { revision: sketch.revision, strokes })) })}>Save</Button>
        <Button disabled={!dirty || busy} onClick={() => setStrokes(sketch.strokes)}>Discard changes</Button>
        <Button disabled={!!dirty || busy} onClick={() => void action(async () => { const result = await request('/' + sketch.id + '/export', 'POST', { revision: sketch.revision }); setDownload('/api/artifacts/' + result.artifact_id + '/raw?version=' + result.version) })}>Export PNG</Button>
        {download && <a href={download} download="sketch.png">Download PNG</a>}
      </div>
      <button disabled={busy || !!dirty} onClick={() => { setBusy(true); fetch('/api/capabilities/media/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation: 'sketch_export', sketch_id: sketch.id, revision: sketch.revision, request_id: crypto.randomUUID() }) }).then(async response => { const value = await response.json(); if (!response.ok) throw new Error(value.error); location.hash = '#/capabilities/media?view=jobs' }).catch(reason => setError(String(reason))).finally(() => setBusy(false)) }}>Queue PNG export</button>
      <p role="status">Revision {sketch.revision}{dirty ? ' · Unsaved changes' : ' · Saved'}. Erase removes drawing only; the original image stays intact.</p>
      <div className="relative max-w-full" style={{ width: sketch.width, aspectRatio: `${sketch.width}/${sketch.height}`, background: 'white' }}>
        {sketch.source_artifact_id && <img alt="Original image" src={base + '/' + sketch.id + '/source'} className="absolute inset-0 w-full h-full" onError={() => setError('Original image is unavailable')} />}
        <canvas aria-label="Drawing canvas" ref={canvas} width={sketch.width} height={sketch.height} className="relative w-full h-full touch-none" onPointerDown={e => {
          if (busy || strokes.length >= 500) return
          e.currentTarget.setPointerCapture(e.pointerId); active.current = { tool, color, width, points: [point(e)] }; setStrokes(old => [...old, active.current!])
        }} onPointerMove={e => { if (active.current && active.current.points.length < 5000) { active.current = { ...active.current, points: [...active.current.points, point(e)] }; setStrokes(old => [...old.slice(0, -1), active.current!]) } }} onPointerUp={() => { active.current = null }} onPointerCancel={() => { active.current = null }} />
      </div>
    </>}
  </section>
}

export default function Page() {
  const [view, setView] = useState(() => typeof location !== 'undefined' && new URLSearchParams(location.hash.split('?')[1] || '').get('view'))
  useEffect(() => { const update = () => setView(new URLSearchParams(location.hash.split('?')[1] || '').get('view')); window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update) }, [])
  return view === 'videos' ? <VideoPage /> : view === 'cleanup' ? <CleanupPage /> : view === 'datasets' ? <DatasetsPage /> : view === 'images' ? <ImagePage /> : view === 'readiness' ? <Readiness /> : view === 'jobs' ? <JobsPage /> : view === 'library' ? <LibraryPage /> : <SketchPage />
}
