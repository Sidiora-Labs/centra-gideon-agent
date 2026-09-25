import { useEffect, useRef, useState } from 'react'
import { gatewayHeaders, requestJson, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { ListScaffold } from '../../../shared/ui/ListScaffold'
import { Field, Select, TextInput } from '../../../shared/ui/forms'
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
  return <main className="h-full"><ListScaffold title="Capture inbox" bodyClassName="mx-auto px-l py-l"><div className="flex flex-col gap-xl">
    <p data-type="body-m" className="max-w-[42rem] text-on-surface-var">Your original words and recordings stay preserved as you review and organize them.</p>
    {error ? <div role="alert" className="flex flex-wrap items-center gap-s border-l-2 border-danger/40 pl-s text-danger"><p>{error}</p><Button variant="secondary" onClick={() => setReload(value => value + 1)}>Reload captures</Button></div> : null}
    <section className="flex flex-col gap-m rounded-lg bg-surface-container p-l"><label data-type="label-s" className="grid gap-xs text-on-surface-var">Capture text<textarea className="min-h-40 rounded-md border border-outline-variant/30 bg-surface-high p-m text-on-surface" value={text} maxLength={100000} onChange={event => { setText(event.target.value); captureKey.current = crypto.randomUUID() }} /></label>
    <Button className="w-fit" disabled={busy || !text.trim()} onClick={() => act(() => requestJson<Capture>(root, 'POST', { request_id: captureKey.current, text }), true)}>Save text</Button></section>
    <details className="rounded-lg bg-surface-container"><summary data-type="label-l" className="cursor-pointer px-l py-m">Capture voice</summary><div className="flex flex-col gap-m border-t border-outline-variant/20 p-l"><AudioRecorder onRecorded={file => { setAudio(file); captureKey.current = crypto.randomUUID() }} onClear={() => setAudio(null)} />
      <label data-type="label-s" className="grid gap-xs text-on-surface-var">Audio file<input className="rounded-md border border-outline-variant/30 bg-surface-high p-s" type="file" accept="audio/*" onChange={event => { setAudio(event.target.files?.[0] || null); captureKey.current = crypto.randomUUID() }} /></label>
      <Button disabled={busy || !audio} onClick={saveAudio}>Save recording</Button>
    </div></details>
    {!items && !error ? <p role="status">Loading captures…</p> : null}
    {items ? <section aria-label="Capture history" className="rounded-lg bg-surface-container p-l"><h2 data-type="title-m">{items.total ? `${items.total} captures` : 'No captures yet.'}</h2><ul>{items.items.map(item => <li key={item.id} className="border-b border-outline-variant/20 py-s last:border-0"><button className="w-full rounded-md p-s text-left hover:bg-surface-high" onClick={() => choose(item)}><span data-type="caption" className="text-on-surface-low"><time>{new Date(item.captured_at).toLocaleString()}</time> · {item.input_origin} · {item.status}</span><p className="text-on-surface-var">{(item.transcript || item.text || 'Original recording').slice(0, 120)}</p></button></li>)}</ul>
      <div className="flex gap-s pt-m"><Button variant="secondary" disabled={!offset || busy} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous</Button><Button variant="secondary" disabled={items.next_offset === null || busy} onClick={() => setOffset(items.next_offset ?? offset)}>Next</Button></div>
    </section> : null}
    {selected ? <section aria-label="Review capture" className="flex flex-col gap-m rounded-lg bg-surface-container p-l">
      <h2 data-type="title-m">Review capture</h2><p className="text-on-surface-var">Original: {selected.text || 'Voice recording'}</p>
      {selected.audio_item_id ? <><audio controls src={`/api/knowledge/items/${encodeURIComponent(selected.audio_item_id)}/file`} /><Button disabled={busy || Boolean(selected.transcript)} onClick={() => act(() => requestJson<Capture>(`${root}/${selected.id}/transcribe`, 'POST'))}>Transcribe original recording</Button></> : null}
      {selected.error ? <p role="status">{selected.error}</p> : null}
      <Field label="Destination"><Select value={destination} surface="high" onChange={value => revise(() => setDestination(value))} options={[{value:'note',label:'Note'},{value:'journal',label:'Journal'},{value:'fleeting',label:'Fleeting idea'}]} /></Field>
      <Field label="Title"><TextInput value={title} maxLength={300} surface="high" onChange={value => revise(() => setTitle(value))} /></Field>
      <label data-type="label-s" className="grid gap-xs text-on-surface-var">Reviewed text<textarea className="min-h-56 rounded-md border border-outline-variant/30 bg-surface-high p-m text-on-surface" value={content} maxLength={100000} onChange={event => revise(() => setContent(event.target.value))} /></label>
      <Button disabled={busy || !title.trim() || !content.trim()} onClick={() => act(() => requestJson<Capture>(`${root}/${selected.id}/route`, 'POST', { request_id: routeKey.current, revision: selected.revision, destination, title, content }))}>Save reviewed destination</Button>
      {selected.source_link ? <a className="text-primary underline" href={selected.source_link}>Open saved knowledge</a> : null}
      <p>{selected.events.length} routing revisions · original captured {selected.captured_at}</p>
    </section> : null}
  </div></ListScaffold></main>
}
