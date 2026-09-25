import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Proposal = { id: string; mode: string; summary: string; base_revision: number; original: string; replacement: string; diff: string; diff_truncated: boolean; missing: boolean; promotion: unknown }
const control = 'w-full rounded-md border border-outline-variant/30 bg-surface-container px-m py-s text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'
export default function Polishing({ id, revision, text, apiRoot, onPromoted }: { id: string; revision: number; text: string; apiRoot: string; onPromoted: () => void }) {
  const t = (value: string) => value
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
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : t('Polishing request failed'))
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
  return <section className="space-y-5 rounded-lg bg-surface-container p-l" aria-label={t('Bounded manuscript polishing')}><header><h2 data-type="title-m" className="text-on-surface">{t('Bounded manuscript polishing')}</h2>
    <p className="mt-1 text-sm text-on-surface-variant">{t('Polish a passage of up to 4000 characters from the saved active draft. Review the candidate before promotion.')}</p></header>
    {error && <p role="alert">{error}</p>}
    <div className="grid gap-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]"><section aria-label={t('Prepare polishing candidate')} className="space-y-3 rounded-md bg-surface-high p-l">
    <label className="block">{t('Polishing mode')}<select className={control} value={mode} onChange={e => { setMode(e.target.value); setRequestId(crypto.randomUUID()) }}><option value="model">{t('Configured model')}</option><option value="authored">{t('My revision')}</option></select></label>
    <label className="block">{t('Passage start')}<input className={control} type="number" min={0} value={start} onChange={e => { setStart(Number(e.target.value)); setRequestId(crypto.randomUUID()) }} /></label>
    <label className="block">{t('Passage end')}<input className={control} type="number" min={1} value={end} onChange={e => { setEnd(Number(e.target.value)); setRequestId(crypto.randomUUID()) }} /></label>
    <label className="block">{t('Selected saved passage')}<textarea className={control} readOnly value={text.slice(start, end)} /></label>
    <label className="block">{t('Polishing instruction')}<textarea className={control} value={instruction} onChange={e => { setInstruction(e.target.value); setRequestId(crypto.randomUUID()) }} /></label>
    {mode === 'authored' && <label className="block">{t('My replacement passage')}<textarea className={control} value={replacement} onChange={e => { setReplacement(e.target.value); setRequestId(crypto.randomUUID()) }} /></label>}
    <Button disabled={busy || end <= start || end - start > 4000 || end > text.length} onClick={() => void propose()}>{t('Prepare polishing candidate')}</Button>
    </section><section aria-label={t('Polishing candidates')} className="space-y-3"><h3 data-type="label-l" className="text-on-surface">{t('Polishing candidates')}</h3>{items.length === 0 && <p className="text-sm text-on-surface-variant">{t('No polishing candidates yet.')}</p>}<ul className="space-y-2">{items.map((item, index) => <li key={item.id} className="rounded-lg border border-outline-variant bg-surface p-3"><Button disabled={busy} onClick={() => void open(item.id)}>{t('Review candidate')} {items.length - index}</Button> <span className="text-sm text-on-surface-variant">{t(item.mode)} · {item.summary}</span></li>)}</ul>
    {selected && <section aria-label={t('Polishing review')} className="space-y-3"><h3>{t('Polishing review')}</h3><p>{selected.summary}</p><p>{t('Base work revision')} {selected.base_revision}</p>
      {selected.missing ? <p>{t('Candidate or base artifact missing')}</p> : <><label className="block">{t('Original passage')}<textarea className={control} readOnly value={selected.original} /></label><label className="block">{t('Candidate passage')}<textarea className={control} readOnly value={selected.replacement} /></label><pre className="whitespace-pre-wrap break-words">{selected.diff}</pre>{selected.diff_truncated && <p>{t('Diff excerpt limited to 20000 characters')}</p>}</>}
      {selected.promotion ? <p>{t('Candidate promoted')}</p> : <Button disabled={busy || selected.missing || selected.base_revision !== revision} onClick={() => void promote()}>{t('Promote reviewed candidate')}</Button>}
    </section>}</section></div>
  </section>
}
