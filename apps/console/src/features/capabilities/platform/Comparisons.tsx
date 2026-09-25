import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
interface Observation { id: string; model: string; corpus: string; metric: string; score: number; source: string; methodology: string; observed_at: string }
interface Snapshot { imports: Observation[]; runs: string[]; selected_run: string | null; benchmark: { rows: { tier: string; rubric_class: string; agreement: number | null; cost_usd: number | null; wall_secs: number | null; verifier_absent: number; protocol_errors: number }[] } | null }
const fields = ['model', 'corpus', 'metric', 'score', 'source', 'observed_at', 'methodology'] as const
export default function Comparisons({ baseUrl = '' }: { baseUrl?: string }) {
  const [data, setData] = useState<Snapshot>(); const [draft, setDraft] = useState<Record<string, string>>({}); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const url = `${baseUrl}/api/capabilities/platform/comparisons`
  const load = (run = '') => gatewayRequest(`${url}${run ? `?run=${encodeURIComponent(run)}` : ''}`).then(readJson<Snapshot>).then(setData).catch(reason => setError(String(reason)))
  useEffect(() => { void load() }, [baseUrl])
  const write = async (id?: string) => {
    setBusy(true); setError('')
    try { setData(await readJson<Snapshot>(await gatewayRequest(id ? `${url}/${id}` : url, id ? 'DELETE' : 'POST', id ? undefined : { ...draft, score: Number(draft.score) }))); if (!id) setDraft({}) }
    catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  const groups = [...new Set((data?.imports || []).map(row => JSON.stringify([row.corpus, row.metric])))]
  return <section aria-label="Model comparisons" className="space-y-m">
    <h2>Model comparison records</h2><p>Imported observations retain their source and methodology. Only identical corpus and metric labels share a table. These records do not change routing.</p>
    {error && <p role="alert">{error}</p>}
    <div className="grid gap-s sm:grid-cols-2">{fields.map(field => <label key={field}>{field.replace('_', ' ')}<input className="block w-full bg-surface-high p-s" aria-label={`Observation ${field}`} type={field === 'observed_at' ? 'date' : 'text'} value={draft[field] || ''} onChange={event => setDraft({ ...draft, [field]: event.target.value })} /></label>)}</div>
    <Button disabled={busy || fields.some(field => !draft[field])} onClick={() => void write()}>Add imported observation</Button>
    {data && !data.imports.length && <p>No imported observations.</p>}
    {groups.map(group => <div key={group} className="overflow-auto"><h3>{JSON.parse(group).join(' · ')}</h3><table><thead><tr><th>Model</th><th>Score</th><th>Source and date</th><th>Methodology</th><th>Action</th></tr></thead><tbody>{data?.imports.filter(row => JSON.stringify([row.corpus, row.metric]) === group).map(row => <tr key={row.id}><td>{row.model}</td><td>{row.score}</td><td><a href={row.source} target="_blank" rel="noreferrer">Imported source</a> {row.observed_at}</td><td>{row.methodology}</td><td><Button disabled={busy} onClick={() => void write(row.id)}>Remove {row.model}</Button></td></tr>)}</tbody></table></div>)}
    <h3>Recorded judge benchmarks</h3>
    <p>Recorded tables identify judge tiers. Served model identity and token usage are unavailable in this table format. Empty metrics remain unknown.</p>
    {data && !data.runs.length && <p>No recorded judge benchmarks.</p>}
    {!!data?.runs.length && <label>Benchmark run<select aria-label="Benchmark run" value={data.selected_run || ''} onChange={event => void load(event.target.value)}>{data.runs.map(run => <option key={run}>{run}</option>)}</select></label>}
    {data?.benchmark && <div className="overflow-auto"><table><thead><tr><th>Tier</th><th>Rubric</th><th>Agreement</th><th>Cost USD</th><th>Seconds</th><th>Missing verifier</th><th>Protocol errors</th></tr></thead><tbody>{data.benchmark.rows.map((row, index) => <tr key={index}><td>{row.tier}</td><td>{row.rubric_class}</td><td>{row.agreement ?? 'Unknown'}</td><td>{row.cost_usd ?? 'Unknown'}</td><td>{row.wall_secs ?? 'Unknown'}</td><td>{row.verifier_absent}</td><td>{row.protocol_errors}</td></tr>)}</tbody></table></div>}
  </section>
}
