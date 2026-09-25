import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Proposal = { id: string; mode: string; summary: string; base_revision: number; original: string; replacement: string; diff: string; diff_truncated: boolean; missing: boolean; promotion: unknown }
const control = 'w-full rounded border border-outline bg-surface p-2 text-on-surface'
export default function Polishing({ id, revision, text, apiRoot, onPromoted }: { id: string; revision: number; text: string; apiRoot: string; onPromoted: () => void }) {
  const [items, setItems] = useState<Proposal[]>([])
  const [selected, setSelected] = useState<Proposal | null>(null)
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(Math.min(text.length, 4000))
  const [mode, setMode] = useState('model')
  const [instruction, setInstruction] = useState('')
  const [replacement, setReplacement] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'Polishing request failed')
  useEffect(() => {
    let alive = true; setSelected(null); setError(''); setStart(0); setEnd(Math.min(text.length, 4000)); setRequestId(crypto.randomUUID())
    requestJson<{ items: Proposal[] }>(`${apiRoot}/${id}/polishing`).then(result => { if (alive) setItems(result.items) }).catch(e => { if (alive) fail(e) })
    return () => { alive = false }
  }, [apiRoot, id, revision, refresh])
  async function open(proposalId: string) { try { setSelected(await requestJson<Proposal>(`${apiRoot}/${id}/polishing/${proposalId}`)) } catch (e) { fail(e) } }
  async function propose() {
    setBusy(true); setError('')
    try {
      const result = await requestJson<Proposal>(`${apiRoot}/${id}/polishing`, 'POST', { request_id: requestId, revision, start, end, mode, instruction, ...(mode === 'authored' ? { replacement } : {}) })
      const list = await requestJson<{ items: Proposal[] }>(`${apiRoot}/${id}/polishing`); setItems(list.items)
      await open(result.id); setRequestId(crypto.randomUUID())
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function promote() {
    if (!selected) return
    setBusy(true); setError('')
    try { await requestJson(`${apiRoot}/${id}/polishing/${selected.id}/promote`, 'POST', { revision }); setSelected(null); setRefresh(v => v + 1); onPromoted() } catch (e) { fail(e) } finally { setBusy(false) }
  }
  return <section className="space-y-3 rounded border border-outline p-3" aria-label="Bounded manuscript polishing"><h2>Bounded manuscript polishing</h2>
    <p>Polish a passage of up to 4000 characters from the saved active draft. Review the candidate before promotion.</p>
    {error && <p role="alert">{error}</p>}
    <label className="block">Polishing mode<select className={control} value={mode} onChange={e => { setMode(e.target.value); setRequestId(crypto.randomUUID()) }}><option value="model">Configured model</option><option value="authored">My revision</option></select></label>
    <label className="block">Passage start<input className={control} type="number" min={0} value={start} onChange={e => { setStart(Number(e.target.value)); setRequestId(crypto.randomUUID()) }} /></label>
    <label className="block">Passage end<input className={control} type="number" min={1} value={end} onChange={e => { setEnd(Number(e.target.value)); setRequestId(crypto.randomUUID()) }} /></label>
    <label className="block">Selected saved passage<textarea className={control} readOnly value={text.slice(start, end)} /></label>
    <label className="block">Polishing instruction<textarea className={control} value={instruction} onChange={e => { setInstruction(e.target.value); setRequestId(crypto.randomUUID()) }} /></label>
    {mode === 'authored' && <label className="block">My replacement passage<textarea className={control} value={replacement} onChange={e => { setReplacement(e.target.value); setRequestId(crypto.randomUUID()) }} /></label>}
    <Button disabled={busy || end <= start || end - start > 4000 || end > text.length} onClick={() => void propose()}>Prepare polishing candidate</Button>
    <ul>{items.map((item, index) => <li key={item.id}><Button disabled={busy} onClick={() => void open(item.id)}>Review candidate {items.length - index}</Button> {item.mode} · {item.summary}</li>)}</ul>
    {selected && <section aria-label="Polishing review" className="space-y-3"><h3>Polishing review</h3><p>{selected.summary}</p><p>Base work revision {selected.base_revision}</p>
      {selected.missing ? <p>Candidate or base artifact missing</p> : <><label className="block">Original passage<textarea className={control} readOnly value={selected.original} /></label><label className="block">Candidate passage<textarea className={control} readOnly value={selected.replacement} /></label><pre className="whitespace-pre-wrap break-words">{selected.diff}</pre>{selected.diff_truncated && <p>Diff excerpt limited to 20000 characters</p>}</>}
      {selected.promotion ? <p>Candidate promoted</p> : <Button disabled={busy || selected.missing || selected.base_revision !== revision} onClick={() => void promote()}>Promote reviewed candidate</Button>}
    </section>}
  </section>
}
