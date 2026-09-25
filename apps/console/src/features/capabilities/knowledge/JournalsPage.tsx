import { useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { ListScaffold } from '../../../shared/ui/ListScaffold'
import { Field, TextArea, TextInput } from '../../../shared/ui/forms'

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
  return <main className="h-full"><ListScaffold title="Daily journals" bodyClassName="mx-auto px-l py-l"><div className="flex flex-col gap-xl"><p data-type="body-m" className="max-w-[42rem] text-on-surface-var">One canonical journal per date and timezone. Activity drafts contain actual source citations and remain editable before saving.</p>{error && <p role="alert" className="border-l-2 border-danger/40 pl-s text-danger">{error}</p>}
    <section className="grid gap-m rounded-lg bg-surface-container p-l sm:grid-cols-2"><label data-type="label-s" className="grid gap-xs text-on-surface-var">Journal date<input className="min-h-10 rounded-md border border-outline-variant/30 bg-surface-high px-m" aria-label="Journal date" type="date" value={date} disabled={busy} onChange={e => { setDate(e.target.value); changed() }} /></label>
    <Field label="Timezone"><TextInput ariaLabel="Timezone" value={zone} surface="high" disabled={busy} onChange={value => { setZone(value); changed() }} /></Field>
    <Button disabled={busy || !date || !zone} onClick={() => void open()}>Open journal</Button>
    </section>{loaded && <section aria-label="Journal editor" className="flex flex-col gap-m rounded-lg bg-surface-container p-l"><Field label="Title"><TextInput ariaLabel="Title" value={title} surface="high" disabled={busy} onChange={value => { setTitle(value); setSaved(false); requestId.current = crypto.randomUUID() }} /></Field><Field label="Journal text"><TextArea ariaLabel="Journal text" rows={12} surface="high" value={content} disabled={busy} onChange={value => { setContent(value); setSaved(false); requestId.current = crypto.randomUUID() }} /></Field><div className="flex flex-wrap gap-s"><Button variant="secondary" disabled={busy} onClick={() => void activity()}>Preview activity draft</Button><Button disabled={busy || !title.trim() || !content.trim() || saved} onClick={() => void save()}>Save journal</Button></div>{journal && <a className="text-primary underline" href={journal.source_link}>Open canonical journal</a>}{saved && <p role="status">Journal saved · revision {journal?.revision}</p>}</section>}
    {draft && <section aria-label="Activity draft" className="flex flex-col gap-m rounded-lg bg-surface-container p-l"><h2 data-type="title-m">Review source activity</h2>{draft.limitations.map(text => <p key={text}>{text}</p>)}{draft.truncated.length > 0 && <p role="status">Source scan limit reached: {draft.truncated.join(', ')}</p>}<pre className="whitespace-pre-wrap rounded-lg bg-surface-high p-m">{draft.content}</pre>{draft.sources.map(row => <p key={row.source_id}><a className="text-primary underline" href={row.source_link}>{row.title}</a></p>)}<Button className="w-fit" disabled={busy || accepted === draft.preview_id} onClick={append}>Append reviewed draft</Button></section>}
  </div></ListScaffold></main>
}
