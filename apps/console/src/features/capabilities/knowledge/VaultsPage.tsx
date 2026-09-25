import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Vault = { id: string; name: string; spec: { path: string }; enabled: boolean; note_count: number }
type Note = { path: string; title: string; content: string; current_hash: string; tags: string[]; wikilinks: string[]; source_link: string }
const root = '/api/capabilities/knowledge/vaults'

export default function VaultsPage() {
  const [vaults, setVaults] = useState<Vault[]>([]), [roots, setRoots] = useState<string[]>([]), [selected, setSelected] = useState<Vault | null>(null), [notes, setNotes] = useState<Note[]>([]), [note, setNote] = useState<Note | null>(null)
  const [name, setName] = useState(''), [path, setPath] = useState(''), [notePath, setNotePath] = useState(''), [content, setContent] = useState(''), [query, setQuery] = useState('')
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [status, setStatus] = useState('')
  const requestId = useRef(crypto.randomUUID())
  async function load() { const data = await requestJson<{ items: Vault[]; allowed_roots: string[] }>(root); setVaults(data.items); setRoots(data.allowed_roots); setSelected(current => current ? data.items.find(item => item.id === current.id) ?? null : current) }
  useEffect(() => { load().catch(e => setError(e.message)) }, [])
  async function act(fn: () => Promise<void>) { setBusy(true); setError(''); try { await fn() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  async function register() { await act(async () => { const value = await requestJson<Vault>(root + '/register', 'POST', { name, path }); await load(); setSelected(value); setStatus('Vault registered') }) }
  async function scan() { if (!selected) return; await act(async () => { const result = await requestJson<{ notes: Note[]; deleted_refs: number }>(`${root}/${selected.id}/scan`, 'POST'); setNotes(result.notes); setStatus(`${result.notes.length} notes indexed · ${result.deleted_refs} missing references archived`) }) }
  async function open(path: string) { if (!selected) return; await act(async () => { const value = await requestJson<Note>(`${root}/${selected.id}/read`, 'POST', { path }); setNote(value); setNotePath(value.path); setContent(value.content); requestId.current = crypto.randomUUID() }) }
  async function save() { if (!selected) return; await act(async () => { const value = await requestJson<Note>(`${root}/${selected.id}/write`, 'POST', { request_id: requestId.current, path: notePath, content, expected_hash: note?.current_hash || '' }); setNote(value); setStatus(value.path + ' saved atomically'); requestId.current = crypto.randomUUID(); await scan() }) }
  async function removeNote() { if (!selected || !note) return; await act(async () => { await requestJson(`${root}/${selected.id}/delete`, 'POST', { request_id: requestId.current, path: note.path, expected_hash: note.current_hash }); setNote(null); setContent(''); setStatus('Note deleted; canonical reference archived'); requestId.current = crypto.randomUUID(); await scan() }) }
  async function search() { if (!selected) return; await act(async () => { const result = await requestJson<{ results: Note[] }>(`${root}/${selected.id}/search`, 'POST', { query }); setNotes(result.results) }) }
  return <main className="mx-auto max-w-4xl space-y-5 p-6"><h1 className="text-2xl font-semibold">External knowledge vaults</h1><p>Register an explicitly allowed Markdown vault, index canonical Knowledge references, and edit with conflict protection.</p>{error && <p role="alert">{error}</p>}{status && <p role="status">{status}</p>}<p>Allowed roots: {roots.join(', ') || 'None configured'}</p>
    <section aria-label="Vault registration"><label>Vault name<input aria-label="Vault name" value={name} onChange={e => setName(e.target.value)} /></label><label>Vault path<input aria-label="Vault path" value={path} onChange={e => setPath(e.target.value)} /></label><Button disabled={busy || !name || !path || roots.length === 0} onClick={() => void register()}>Register vault</Button></section>
    <nav aria-label="External vaults">{vaults.map(item => <Button key={item.id} disabled={busy || !item.enabled} onClick={() => { setSelected(item); setNotes([]); setNote(null) }}>{item.name} · {item.note_count}</Button>)}</nav>
    {selected && <section aria-label="Selected vault"><h2>{selected.name}</h2><p>{selected.spec.path}</p><Button disabled={busy} onClick={() => void scan()}>Scan vault</Button><label>Search notes<input aria-label="Search notes" value={query} onChange={e => setQuery(e.target.value)} /></label><Button disabled={busy || !query} onClick={() => void search()}>Search</Button><ul>{notes.map(item => <li key={item.path}><Button disabled={busy} onClick={() => void open(item.path)}>{item.path}</Button></li>)}</ul></section>}
    {selected && <section aria-label="Vault note editor"><label>Note path<input aria-label="Note path" value={notePath} onChange={e => { setNotePath(e.target.value); setNote(null); requestId.current = crypto.randomUUID() }} /></label><label>Markdown content<textarea aria-label="Markdown content" value={content} onChange={e => { setContent(e.target.value); requestId.current = crypto.randomUUID() }} /></label><Button disabled={busy || !notePath} onClick={() => void save()}>{note ? 'Save note' : 'Create note'}</Button>{note && <><a href={note.source_link}>Open canonical reference</a><Button disabled={busy} onClick={() => void removeNote()}>Delete note</Button></>}</section>}
  </main>
}
