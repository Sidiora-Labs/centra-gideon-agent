import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import { gatewayHeaders, readJson } from '../../../shared/data/gatewayRequest'

type Envelope = { format: string; ciphertext: string }
type Inventory = { groups: { id: string; count: number; cap_chars: number }[]; exclusions: string[] }
type Preview = { preview_token: string; can_apply: boolean; groups: { id: string; new: string[]; duplicates: string[]; tombstoned: string[]; blocked: boolean }[] }
type Receipt = { status: string; applied: { slot: string; count: number }[]; skipped: { slot: string; count: number }[]; errors: { slot: string; error: string }[] }
export default function BundlesPage({ endpoint = '/api/capabilities/identity/bundles' }: { endpoint?: string }) {
  const [inventory, setInventory] = useState<Inventory | null>(null)
  const [groups, setGroups] = useState<string[]>(['persona'])
  const [passphrase, setPassphrase] = useState('')
  const [bundle, setBundle] = useState<Envelope | null>(null)
  const [exported, setExported] = useState<Envelope | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [receipt, setReceipt] = useState<Receipt | null>(null)
  const [request, setRequest] = useState(() => crypto.randomUUID())
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const call = async <T,>(path = '', body?: unknown): Promise<T> => readJson<T>(await fetch(endpoint + path, { method: body ? 'POST' : 'GET', headers: { ...gatewayHeaders, 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) }))
  const perform = async (action: () => Promise<void>) => { setBusy(true); setError(''); try { await action() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  useEffect(() => { void perform(async () => setInventory(await call<Inventory>())) }, [endpoint])
  return <section className="p-4 max-w-4xl mx-auto space-y-4 text-on-surface"><h1 className="text-2xl">Encrypted continuity bundles</h1>
    <p>Select canonical continuity notes to transfer. Credentials, provider settings and runtime controls are excluded. Existing notes are preserved; human-removed notes stay removed.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}{!inventory && <p role="status">Loading bundle groups…</p>}
    {inventory && <fieldset className="space-x-4"><legend>Groups to transfer</legend>{inventory.groups.map(row => <label key={row.id}><input type="checkbox" checked={groups.includes(row.id)} onChange={e => { setGroups(e.target.checked ? [...groups, row.id] : groups.filter(value => value !== row.id)); setPreview(null); setReceipt(null) }} /> {row.id} ({row.count} live notes)</label>)}</fieldset>}
    <label className="block" htmlFor="bundle-passphrase">Bundle passphrase</label><input id="bundle-passphrase" className="w-full bg-surface-high p-2" type="password" autoComplete="off" value={passphrase} onChange={e => { setPassphrase(e.target.value); setPreview(null) }} /><p>Use 12–1024 characters. Keep this passphrase; it is not stored by the bundle service.</p>
    <Button disabled={busy || !groups.length || passphrase.length < 12} onClick={() => void perform(async () => { setExported(await call<Envelope>('/export', { groups, passphrase })); setPassphrase('') })}>Encrypt selected notes</Button>
    {exported && <a className="block underline" download="continuity.gideon.json" href={'data:application/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(exported))}>Download encrypted bundle</a>}
    <label className="block" htmlFor="bundle-file">Import encrypted bundle file</label><input id="bundle-file" type="file" accept="application/json,.json" onChange={e => { const file = e.target.files?.[0]; setPreview(null); setReceipt(null); setRequest(crypto.randomUUID()); if (file) void perform(async () => { if (file.size > 70000) throw new Error('Bundle file exceeds size limit'); const content = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(new Error("Could not read bundle file")); reader.readAsText(file) }); setBundle(JSON.parse(content)) }) }} />
    {bundle && <p>Encrypted bundle loaded.</p>}
    <Button disabled={busy || !bundle || !groups.length || passphrase.length < 12} onClick={() => void perform(async () => { setPreview(await call<Preview>('/preview', { bundle, passphrase, groups })); setRequest(crypto.randomUUID()); setReceipt(null) })}>Preview selected import</Button>
    {preview && <section aria-label="Bundle preview"><h2>Review import</h2>{preview.groups.map(group => <article key={group.id}><h3>{group.id}</h3><p>{group.new.length} new · {group.duplicates.length} duplicates · {group.tombstoned.length} human-removed</p>{group.new.map(text => <p key={text}>{text}</p>)}{group.blocked && <p>Destination slot capacity exceeded.</p>}</article>)}
      <Button disabled={busy || !preview.can_apply || !!receipt} onClick={() => void perform(async () => { setReceipt(await call<Receipt>('/apply', { bundle, passphrase, groups, preview_token: preview.preview_token, request_id: request })); setPassphrase(''); setInventory(await call<Inventory>()) })}>Apply reviewed import</Button></section>}
    {receipt && <section aria-label="Import receipt"><h2>Import status: {receipt.status}</h2>{receipt.applied.map(row => <p key={row.slot}>{row.slot}: {row.count} added</p>)}{receipt.skipped.map(row => <p key={row.slot}>{row.slot}: {row.count} skipped</p>)}{receipt.errors.map(row => <p key={row.slot}>{row.slot}: {row.error}</p>)}</section>}
  </section>
}
