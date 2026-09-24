import { useEffect, useRef, useState } from 'react'
import { gatewayHeaders, requestJson, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { AudioRecorder } from '../../knowledge/AudioRecorder'

type Capture = { id: string; text: string; input_origin: string; captured_at: string; status: string; transcript: string | null; error: string | null; revision: number; audio_item_id: string | null; source_link: string | null; events: { event: string; happened_at: string; payload: { title: string; content: string; destination: string } }[] }
type PageResult = { items: Capture[]; total: number; next_offset: number | null }
const root = '/api/capabilities/knowledge/captures'

export default function CapturePage() {
  const [items, setItems] = useState<PageResult | null>(null)
  const [selected, setSelected] = useState<Capture | null>(null)
  const [text, setText] = useState('')
  const [audio, setAudio] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [destination, setDestination] = useState('note')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [offset, setOffset] = useState(0)
  const [reload, setReload] = useState(0)
  const captureKey = useRef(crypto.randomUUID())
  const routeKey = useRef(crypto.randomUUID())
  const choose = (capture: Capture) => {
    const latest = capture.events.at(-1)?.payload
    setSelected(capture); setTitle(latest?.title || (capture.transcript || capture.text).split('\n')[0].slice(0, 100))
    setContent(latest?.content || capture.transcript || capture.text); setDestination(latest?.destination || 'note'); routeKey.current = crypto.randomUUID()
    window.history.replaceState(null, '', `#/capabilities/knowledge/capture?capture=${encodeURIComponent(capture.id)}`)
  }
  useEffect(() => {
    let active = true
    setError('')
    requestJson<PageResult>(`${root}?limit=20&offset=${offset}`).then(result => { if (active) setItems(result) }).catch(reason => { if (active) setError(String(reason)) })
    const identity = new URLSearchParams(window.location.hash.split('?')[1] || '').get('capture')
    if (identity) requestJson<Capture>(`${root}/${encodeURIComponent(identity)}`).then(result => { if (active) choose(result) }).catch(reason => { if (active) setError(String(reason)) })
    return () => { active = false }
  }, [offset, reload])
  const act = async (operation: () => Promise<Capture>, clear = false) => {
    setBusy(true); setError('')
    try {
      const capture = await operation(); choose(capture)
      if (clear) { setText(''); setAudio(null); captureKey.current = crypto.randomUUID() }
      setReload(value => value + 1)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }
  const saveAudio = () => act(async () => {
    const body = new FormData(); body.append('audio', audio!)
    return readJson<Capture>(await fetch(`${root}/audio`, { method: 'POST', headers: { ...gatewayHeaders, 'X-Capture-Request-ID': captureKey.current }, body }))
  }, true)
  const revise = (change: () => void) => { change(); routeKey.current = crypto.randomUUID() }
  return <main className="mx-auto flex w-full max-w-4xl flex-col gap-l p-l">
    <h1 className="text-2xl font-semibold">Capture inbox</h1>
    <p>Your original words and recordings stay preserved as you review and organize them.</p>
    {error ? <div role="alert"><p>{error}</p><Button onClick={() => setReload(value => value + 1)}>Reload captures</Button></div> : null}
    <label className="flex flex-col gap-s">Capture text<textarea className="rounded border border-outline-variant bg-surface-container p-s" value={text} maxLength={100000} onChange={event => { setText(event.target.value); captureKey.current = crypto.randomUUID() }} /></label>
    <Button disabled={busy || !text.trim()} onClick={() => act(() => requestJson<Capture>(root, 'POST', { request_id: captureKey.current, text }), true)}>Save text</Button>
    <details><summary>Capture voice</summary><AudioRecorder onRecorded={file => { setAudio(file); captureKey.current = crypto.randomUUID() }} onClear={() => setAudio(null)} />
      <label>Audio file<input type="file" accept="audio/*" onChange={event => { setAudio(event.target.files?.[0] || null); captureKey.current = crypto.randomUUID() }} /></label>
      <Button disabled={busy || !audio} onClick={saveAudio}>Save recording</Button>
    </details>
    {!items && !error ? <p role="status">Loading captures…</p> : null}
    {items ? <section aria-label="Capture history"><p>{items.total ? `${items.total} captures` : 'No captures yet.'}</p><ul>{items.items.map(item => <li key={item.id} className="border-b border-outline-variant py-s"><button className="w-full text-left" onClick={() => choose(item)}><time>{new Date(item.captured_at).toLocaleString()}</time> · {item.input_origin} · {item.status}<p>{(item.transcript || item.text || 'Original recording').slice(0, 120)}</p></button></li>)}</ul>
      <div className="flex gap-m"><Button disabled={!offset || busy} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous</Button><Button disabled={items.next_offset === null || busy} onClick={() => setOffset(items.next_offset ?? offset)}>Next</Button></div>
    </section> : null}
    {selected ? <section aria-label="Review capture" className="flex flex-col gap-m">
      <h2 className="text-xl">Review capture</h2><p>Original: {selected.text || 'Voice recording'}</p>
      {selected.audio_item_id ? <><audio controls src={`/api/knowledge/items/${encodeURIComponent(selected.audio_item_id)}/file`} /><Button disabled={busy || Boolean(selected.transcript)} onClick={() => act(() => requestJson<Capture>(`${root}/${selected.id}/transcribe`, 'POST'))}>Transcribe original recording</Button></> : null}
      {selected.error ? <p role="status">{selected.error}</p> : null}
      <label>Destination<select value={destination} onChange={event => revise(() => setDestination(event.target.value))}><option value="note">Note</option><option value="journal">Journal</option><option value="fleeting">Fleeting idea</option></select></label>
      <label className="flex flex-col">Title<input value={title} maxLength={300} onChange={event => revise(() => setTitle(event.target.value))} /></label>
      <label className="flex flex-col">Reviewed text<textarea value={content} maxLength={100000} onChange={event => revise(() => setContent(event.target.value))} /></label>
      <Button disabled={busy || !title.trim() || !content.trim()} onClick={() => act(() => requestJson<Capture>(`${root}/${selected.id}/route`, 'POST', { request_id: routeKey.current, revision: selected.revision, destination, title, content }))}>Save reviewed destination</Button>
      {selected.source_link ? <a className="text-primary underline" href={selected.source_link}>Open saved knowledge</a> : null}
      <p>{selected.events.length} routing revisions · original captured {selected.captured_at}</p>
    </section> : null}
  </main>
}
