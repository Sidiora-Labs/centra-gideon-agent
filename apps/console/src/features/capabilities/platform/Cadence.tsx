import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
interface Row { trigger_id: string; name: string; task_class: string | null; base_interval: number; effective_interval: number; samples: number; successes: number; excluded: number; confidence: string; reason: string; evidence: { run_id: string; trigger_id: string; success: boolean }[] }
interface View { revision: number; triggers: Row[] }
export default function Cadence({ baseUrl = '' }: { baseUrl?: string }) {
  const [view, setView] = useState<View>(); const [classes, setClasses] = useState<Record<string, string>>({}); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const url = `${baseUrl}/api/capabilities/platform/cadence`
  useEffect(() => { gatewayRequest(url).then(readJson<View>).then(setView).catch(reason => setError(String(reason))) }, [baseUrl])
  const save = async (row: Row, enabled: boolean) => {
    setBusy(true); setError('')
    try { setView(await readJson<View>(await gatewayRequest(`${url}/${encodeURIComponent(row.trigger_id)}`, 'PUT', { revision: view?.revision, enabled, task_class: classes[row.trigger_id] ?? row.task_class ?? '' }))) } catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  return <section aria-label="Adaptive cadence" className="space-y-m"><h2>Task class cadence</h2><p>Opt in interval triggers to share typed execution evidence. Five observations are required; low success slows cadence up to four times. Three recent successes or a day without evidence restore a normal probe.</p>{error && <p role="alert">{error}</p>}
    {view?.triggers.map(row => <article key={row.trigger_id} aria-label={row.name}><h3>{row.name}</h3><p>{row.base_interval}s base → {row.effective_interval}s effective</p><p>{row.successes}/{row.samples} successful · {row.excluded} excluded · {row.confidence} · {row.reason}</p><label>Task class<input aria-label={`Task class ${row.name}`} value={classes[row.trigger_id] ?? row.task_class ?? ''} onChange={event => setClasses({ ...classes, [row.trigger_id]: event.target.value })} /></label><Button disabled={busy} onClick={() => void save(row, true)}>Enable cadence {row.name}</Button><Button disabled={busy || !row.task_class} onClick={() => void save(row, false)}>Disable cadence {row.name}</Button><details><summary>Execution evidence</summary>{row.evidence.map(item => <p key={item.trigger_id + item.run_id}>{item.trigger_id} / {item.run_id}: {item.success ? 'success' : 'task failure'}</p>)}</details></article>)}
  </section>
}
