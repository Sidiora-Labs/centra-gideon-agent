import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { ListScaffold } from '../../../shared/ui/ListScaffold'
import { Field, TextArea, TextInput } from '../../../shared/ui/forms'

type IdeaList = { id: string; title: string; hash: string; revision: number; status: string; source_link: string; collection_link: string; sync_enabled: boolean; next_fire_at: string; minutes?: number; items: { id: string; content: string; source_link: string }[] }
type Preview = { preview_id: string; document: { id: string; title: string; ideas: string[] }; unsupported_fields: string[] }
const root = '/api/capabilities/knowledge/ideas'

export default function IdeasPage() {
  const [lists, setLists] = useState<IdeaList[]>([])
  const [selected, setSelected] = useState<IdeaList | null>(null)
  const [content, setContent] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [exported, setExported] = useState<{ content: string; filename: string } | null>(null)
  const [vault, setVault] = useState({ available: false, reason: '' })
  const [minutes, setMinutes] = useState(60)
  const [enabled, setEnabled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [outcome, setOutcome] = useState('')
  const importId = useRef(crypto.randomUUID())
  const syncId = useRef(crypto.randomUUID())
  const scheduleId = useRef(crypto.randomUUID())
  useEffect(() => { requestJson<{ items: IdeaList[]; vault: typeof vault }>(root).then(data => { setLists(data.items); setVault(data.vault) }).catch(e => setError(e.message)) }, [])
  function choose(item: IdeaList) { setSelected(item); setMinutes(item.minutes || 60); setEnabled(item.sync_enabled); setExported(null); setOutcome(''); syncId.current = crypto.randomUUID(); scheduleId.current = crypto.randomUUID() }
  function applied(item: IdeaList) { setLists(current => [item, ...current.filter(row => row.id !== item.id)]); choose(item) }
  async function act(action: () => Promise<void>) { setBusy(true); setError(''); try { await action() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  async function review() { await act(async () => { setPreview(await requestJson<Preview>(root + '/preview', 'POST', { content })) }) }
  async function importList() { if (!preview) return; await act(async () => { const previous = lists.find(row => row.id === preview.document.id); const item = await requestJson<IdeaList>(root + '/import', 'POST', { request_id: importId.current, content, preview_id: preview.preview_id, expected_hash: previous?.hash || '' }); applied(item); setPreview(null); importId.current = crypto.randomUUID() }) }
  async function exportList() { if (!selected) return; await act(async () => { setExported(await requestJson(`${root}/${selected.id}/export`)) }) }
  async function sync() { if (!selected) return; await act(async () => { const result = await requestJson<IdeaList & { outcome: string }>(`${root}/${selected.id}/sync`, 'POST', { request_id: syncId.current, expected_hash: selected.hash }); applied(result); setOutcome(result.outcome) }) }
  async function schedule() { if (!selected) return; await act(async () => { applied(await requestJson<IdeaList>(`${root}/${selected.id}/schedule`, 'POST', { request_id: scheduleId.current, revision: selected.revision, enabled, minutes })) }) }
  async function reload() { if (!selected) return; await act(async () => { applied(await requestJson<IdeaList>(`${root}/${selected.id}`)) }) }
  return <main className="h-full"><ListScaffold title="Idea-list exchange" bodyClassName="mx-auto px-l py-l"><div className="flex flex-col gap-xl"><p data-type="body-m" className="max-w-[42rem] text-on-surface-var">Review portable Markdown, preserve ordered ideas in canonical collections, and opt into the existing owned-vault sync.</p>{error && <p role="alert" className="border-l-2 border-danger/40 pl-s text-danger">{error}</p>}{!vault.available && <p role="status" className="text-on-surface-var">{vault.reason}</p>}
    <nav aria-label="Idea lists" className="flex flex-wrap gap-s rounded-lg bg-surface-container p-m">{lists.map(item => <Button variant={selected?.id===item.id?'primary':'ghost'} key={item.id} disabled={busy} onClick={() => choose(item)}>{item.title}</Button>)}</nav>
    <section className="flex flex-col gap-m rounded-lg bg-surface-container p-l"><Field label="Idea-list Markdown"><TextArea ariaLabel="Idea-list Markdown" rows={12} surface="high" disabled={busy} value={content} onChange={value => { setContent(value); setPreview(null); importId.current = crypto.randomUUID() }} /></Field>
    <Button disabled={busy || !content.trim()} onClick={() => void review()}>Preview import</Button>
    {preview && <section aria-label="Import review" className="rounded-lg bg-surface-high p-m"><h2 data-type="title-m">{preview.document.title}</h2><p>{preview.document.ideas.length} ordered ideas</p>{preview.unsupported_fields.length > 0 && <p>Preserved extra metadata: {preview.unsupported_fields.join(', ')}</p>}<Button disabled={busy} onClick={() => void importList()}>Import reviewed list</Button></section>}</section>
    {selected && <section aria-label="Selected idea list" className="flex flex-col gap-m rounded-lg bg-surface-container p-l"><h2 data-type="title-m">{selected.title}</h2><p className="text-on-surface-var">Status: {selected.status}</p><div className="flex flex-wrap gap-m"><a className="text-primary underline" href={selected.collection_link}>Open canonical collection</a><a className="text-primary underline" href={selected.source_link}>Open list source</a></div><ol className="list-decimal space-y-s pl-l">{selected.items.map(item => <li key={item.id}><a className="text-primary underline" href={item.source_link}>{item.content}</a></li>)}</ol><div className="flex flex-wrap gap-s"><Button variant="secondary" disabled={busy} onClick={() => void reload()}>Refresh list</Button><Button variant="secondary" disabled={busy} onClick={() => void exportList()}>Export Markdown</Button><Button disabled={busy || !vault.available} onClick={() => void sync()}>Sync owned vault</Button></div>{outcome && <p role="status">Sync result: {outcome}</p>}<Field label="Sync interval minutes"><TextInput ariaLabel="Sync interval minutes" type="number" min={5} max={1440} surface="high" disabled={busy} value={String(minutes)} onChange={value => { setMinutes(Number(value)); scheduleId.current = crypto.randomUUID() }} /></Field><label className="inline-flex items-center gap-s"><input className="size-4 accent-primary" type="checkbox" aria-label="Recurring sync enabled" disabled={busy || !vault.available} checked={enabled} onChange={e => { setEnabled(e.target.checked); scheduleId.current = crypto.randomUUID() }} />Recurring sync enabled</label><Button className="w-fit" disabled={busy || minutes < 5 || minutes > 1440 || (enabled && !vault.available)} onClick={() => void schedule()}>Save sync schedule</Button><p>{selected.sync_enabled ? `Next sync: ${selected.next_fire_at}` : 'Recurring sync disabled'}</p></section>}
    {exported && <section aria-label="Exported Markdown" className="flex flex-col gap-m rounded-lg bg-surface-container p-l"><label data-type="label-s" className="grid gap-xs">Exported Markdown<textarea className="min-h-48 rounded-md border border-outline-variant/30 bg-surface-high p-m font-mono" aria-label="Exported Markdown" readOnly value={exported.content} /></label><a className="text-primary underline" download={exported.filename} href={'data:text/markdown;charset=utf-8,' + encodeURIComponent(exported.content)}>Download Markdown</a></section>}
  </div></ListScaffold></main>
}
