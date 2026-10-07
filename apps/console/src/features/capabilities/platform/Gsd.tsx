import { Select, TextArea } from '../../../shared/ui/forms'
import { BUSY_REASON } from '../../../shared/ui/unavailable'
import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
interface Detail { project: { id: string; name: string }; documents: string[]; phases: { id: string; plans: string[]; summaries: string[] }[]; document: { name: string; content: string; sha256: string } | null }
export default function Gsd({ baseUrl = '' }: { baseUrl?: string }) {
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]); const [selected, setSelected] = useState(''); const [detail, setDetail] = useState<Detail>(); const [text, setText] = useState(''); const [error, setError] = useState(''); const [receipt, setReceipt] = useState(''); const [busy, setBusy] = useState(false)
  const url = `${baseUrl}/api/capabilities/platform/gsd`
  useEffect(() => { gatewayRequest(url).then(readJson<{ projects: { id: string; name: string }[] }>).then(value => setProjects(value.projects)).catch(reason => setError(String(reason))) }, [baseUrl])
  const load = async (id: string, document = '') => {
    setSelected(id); setError(''); setReceipt(''); setDetail(undefined)
    if (!id) return
    try { const value = await readJson<Detail>(await gatewayRequest(`${url}/${id}${document ? `?document=${encodeURIComponent(document)}` : ''}`)); setDetail(value); setText(value.document?.content || '') } catch (reason) { setError(String(reason)) }
  }
  const save = async () => {
    if (!detail?.document) return
    setBusy(true); setError('')
    try { setDetail(await readJson<Detail>(await gatewayRequest(`${url}/${selected}`, 'PUT', { document: detail.document.name, sha256: detail.document.sha256, content: text }))) } catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  const request = async (phase: string, action: string) => {
    setBusy(true); setError('')
    try { const value = await readJson<{ task: { id: string }; created: boolean }>(await gatewayRequest(`${url}/${selected}/phase`, 'POST', { phase, action })); setReceipt(`${value.created ? 'Created' : 'Existing'} task ${value.task.id}. Execution has not been started by this request.`) } catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  return <section aria-label="GSD planning" className="grid gap-l">
    <h2 data-type="title-m">GSD project planning</h2><p>Edit the project’s original planning documents. Phase buttons create open requests in its GSD task list.</p>
    {error && <p role="alert">{error}</p>}{receipt && <p role="status">{receipt}</p>}
    <label data-type="label-s" className="grid gap-xs">Project<Select className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel="GSD project" value={selected} onChange={value => void load(value)} options={[{ value: "", label: ["Select project"].join('') }, ...(projects.map(project => ({ value: project.id, label: [project.name].join('') })) ?? [])]} /></label>
    {detail && <><div className="flex flex-wrap gap-s">{detail.documents.map(name => <Button key={name} disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void load(selected, name)}>{name}</Button>)}</div>
      {detail.document && <><label data-type="label-s" className="grid gap-xs">{detail.document.name}<TextArea ariaLabel="Planning document text" className="block min-h-48 w-full bg-surface-high p-s" value={text} onChange={value => setText(value)} /></label><Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void save()}>Save planning document</Button><Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void load(selected, detail.document!.name)}>Reload planning document</Button></>}
      {detail.phases.map(phase => <article className="grid gap-s rounded-lg border border-outline-variant/20 bg-surface-container p-l" key={phase.id} aria-label={phase.id}><h3 data-type="headline-s">{phase.id}</h3><p>{phase.plans.length} plan documents · {phase.summaries.length} summary documents</p>{['plan', 'execute', 'verify'].map(action => <Button key={action} disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void request(phase.id, action)}>Request {action} {phase.id}</Button>)}</article>)}
    </>}
  </section>
}
