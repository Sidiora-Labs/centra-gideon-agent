import { useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Journal = { id: string; title: string; content: string; revision: number; fingerprint: string; source_link: string }
type Draft = { content: string; preview_id: string; sources: { source_id: string; title: string; source_link: string }[]; limitations: string[]; truncated: string[] }
const root = '/api/capabilities/knowledge/journals'

export default function JournalsPage() {
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [zone, setZone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
  const [journal, setJournal] = useState<Journal | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [accepted, setAccepted] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const requestId = useRef(crypto.randomUUID())
  function changed() { setLoaded(false); setJournal(null); setDraft(null); setAccepted(''); setSaved(false); requestId.current = crypto.randomUUID() }
  async function act(action: () => Promise<void>) { setBusy(true); setError(''); try { await action() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  function query() { return new URLSearchParams({ date, timezone: zone }).toString() }
  async function open() { await act(async () => { const data = await requestJson<{ journal: Journal | null }>(`${root}?${query()}`); setJournal(data.journal); setTitle(data.journal?.title || `Journal · ${date}`); setContent(data.journal?.content || ''); setLoaded(true); setDraft(null); setAccepted(''); setSaved(false); requestId.current = crypto.randomUUID() }) }
  async function activity() { await act(async () => { setDraft(await requestJson<Draft>(`${root}/draft?${query()}`)) }) }
  function append() { if (!draft) return; setContent(value => (value ? value + '\n\n' : '') + draft.content); setAccepted(draft.preview_id); setSaved(false); requestId.current = crypto.randomUUID() }
  async function save() { await act(async () => { const result = await requestJson<Journal>(root, 'POST', { request_id: requestId.current, date, timezone: zone, title, content, revision: journal?.revision || 0, fingerprint: journal?.fingerprint || '', preview_id: accepted }); setJournal(result); setSaved(true); requestId.current = crypto.randomUUID() }) }
  return <main className="mx-auto max-w-4xl space-y-5 p-6"><h1 className="text-2xl font-semibold">Daily journals</h1><p>One canonical journal per date and timezone. Activity drafts contain actual source citations and remain editable before saving.</p>{error && <p role="alert">{error}</p>}
    <label className="block">Journal date<input aria-label="Journal date" type="date" value={date} disabled={busy} onChange={e => { setDate(e.target.value); changed() }} /></label>
    <label className="block">Timezone<input aria-label="Timezone" value={zone} disabled={busy} onChange={e => { setZone(e.target.value); changed() }} /></label>
    <Button disabled={busy || !date || !zone} onClick={() => void open()}>Open journal</Button>
    {loaded && <section aria-label="Journal editor"><label className="block">Title<input aria-label="Title" value={title} disabled={busy} onChange={e => { setTitle(e.target.value); setSaved(false); requestId.current = crypto.randomUUID() }} /></label><label className="block">Journal text<textarea aria-label="Journal text" className="min-h-60 w-full" value={content} disabled={busy} onChange={e => { setContent(e.target.value); setSaved(false); requestId.current = crypto.randomUUID() }} /></label><Button disabled={busy} onClick={() => void activity()}>Preview activity draft</Button><Button disabled={busy || !title.trim() || !content.trim() || saved} onClick={() => void save()}>Save journal</Button>{journal && <a href={journal.source_link}>Open canonical journal</a>}{saved && <p role="status">Journal saved · revision {journal?.revision}</p>}</section>}
    {draft && <section aria-label="Activity draft"><h2>Review source activity</h2>{draft.limitations.map(text => <p key={text}>{text}</p>)}{draft.truncated.length > 0 && <p role="status">Source scan limit reached: {draft.truncated.join(', ')}</p>}<pre className="whitespace-pre-wrap">{draft.content}</pre>{draft.sources.map(row => <p key={row.source_id}><a href={row.source_link}>{row.title}</a></p>)}<Button disabled={busy || accepted === draft.preview_id} onClick={append}>Append reviewed draft</Button></section>}
  </main>
}
