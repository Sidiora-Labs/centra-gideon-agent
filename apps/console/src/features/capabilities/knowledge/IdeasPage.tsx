import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

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
  return <main className="mx-auto max-w-4xl space-y-5 p-6"><h1 className="text-2xl font-semibold">Idea-list exchange</h1><p>Review portable Markdown, preserve ordered ideas in canonical collections, and opt into the existing owned-vault sync.</p>{error && <p role="alert">{error}</p>}{!vault.available && <p role="status">{vault.reason}</p>}
    <nav aria-label="Idea lists">{lists.map(item => <Button key={item.id} disabled={busy} onClick={() => choose(item)}>{item.title}</Button>)}</nav>
    <label className="block">Idea-list Markdown<textarea aria-label="Idea-list Markdown" className="min-h-60 w-full" disabled={busy} value={content} onChange={e => { setContent(e.target.value); setPreview(null); importId.current = crypto.randomUUID() }} /></label>
    <Button disabled={busy || !content.trim()} onClick={() => void review()}>Preview import</Button>
    {preview && <section aria-label="Import review"><h2>{preview.document.title}</h2><p>{preview.document.ideas.length} ordered ideas</p>{preview.unsupported_fields.length > 0 && <p>Preserved extra metadata: {preview.unsupported_fields.join(', ')}</p>}<Button disabled={busy} onClick={() => void importList()}>Import reviewed list</Button></section>}
    {selected && <section aria-label="Selected idea list"><h2>{selected.title}</h2><p>Status: {selected.status}</p><a href={selected.collection_link}>Open canonical collection</a>{' · '}<a href={selected.source_link}>Open list source</a><ol>{selected.items.map(item => <li key={item.id}><a href={item.source_link}>{item.content}</a></li>)}</ol><Button disabled={busy} onClick={() => void reload()}>Refresh list</Button><Button disabled={busy} onClick={() => void exportList()}>Export Markdown</Button><Button disabled={busy || !vault.available} onClick={() => void sync()}>Sync owned vault</Button>{outcome && <p role="status">Sync result: {outcome}</p>}<label className="block">Sync interval minutes<input aria-label="Sync interval minutes" type="number" min="5" max="1440" disabled={busy} value={minutes} onChange={e => { setMinutes(Number(e.target.value)); scheduleId.current = crypto.randomUUID() }} /></label><label><input type="checkbox" aria-label="Recurring sync enabled" disabled={busy || !vault.available} checked={enabled} onChange={e => { setEnabled(e.target.checked); scheduleId.current = crypto.randomUUID() }} />Recurring sync enabled</label><Button disabled={busy || minutes < 5 || minutes > 1440 || (enabled && !vault.available)} onClick={() => void schedule()}>Save sync schedule</Button><p>{selected.sync_enabled ? `Next sync: ${selected.next_fire_at}` : 'Recurring sync disabled'}</p></section>}
    {exported && <section aria-label="Exported Markdown"><textarea aria-label="Exported Markdown" readOnly value={exported.content} /><a download={exported.filename} href={'data:text/markdown;charset=utf-8,' + encodeURIComponent(exported.content)}>Download Markdown</a></section>}
  </main>
}
