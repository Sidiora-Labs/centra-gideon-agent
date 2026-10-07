import { Select, TextInput } from '../../../shared/ui/forms'
import { BUSY_REASON } from '../../../shared/ui/unavailable'
import { Table, THead, Th, Td } from '../../../shared/ui/Table'
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
  return <section aria-label="Model comparisons" className="grid gap-l">
    <h2 data-type="title-m">Model comparison records</h2><p>Imported observations retain their source and methodology. Only identical corpus and metric labels share a table. These records do not change routing.</p>
    {error && <p role="alert">{error}</p>}
    <div className="grid gap-s sm:grid-cols-2">{fields.map(field => <label className="grid gap-xs text-sm" key={field}>{field.replace('_', ' ')}<TextInput className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel={`Observation ${field}`} type={field === 'observed_at' ? 'date' : 'text'} value={draft[field] || ''} onChange={value => setDraft({ ...draft, [field]: value })} /></label>)}</div>
    <Button disabled={busy || fields.some(field => !draft[field])} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void write()}>Add imported observation</Button>
    {data && !data.imports.length && <p>No imported observations.</p>}
    {groups.map(group => <div key={group} className="overflow-auto"><h3 data-type="headline-s">{JSON.parse(group).join(' · ')}</h3><Table caption={`Imported model comparisons: ${JSON.parse(group).join(' · ')}`} className="w-full min-w-[640px] text-sm"><THead><tr><Th>Model</Th><Th>Score</Th><Th>Source and date</Th><Th>Methodology</Th><Th>Action</Th></tr></THead><tbody>{data?.imports.filter(row => JSON.stringify([row.corpus, row.metric]) === group).map(row => <tr key={row.id}><Td>{row.model}</Td><Td>{row.score}</Td><Td><a href={row.source} target="_blank" rel="noreferrer">Imported source</a> {row.observed_at}</Td><Td>{row.methodology}</Td><Td><Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void write(row.id)}>Remove {row.model}</Button></Td></tr>)}</tbody></Table></div>)}
    <h3 data-type="headline-s">Recorded judge benchmarks</h3>
    <p>Recorded tables identify judge tiers. Served model identity and token usage are unavailable in this table format. Empty metrics remain unknown.</p>
    {data && !data.runs.length && <p>No recorded judge benchmarks.</p>}
    {!!data?.runs.length && <label className="grid gap-xs text-sm">Benchmark run<Select className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel="Benchmark run" value={data.selected_run || ''} onChange={value => void load(value)} options={[...(data.runs.map(run => ({ value: [run].join(''), label: [run].join('') })) ?? [])]} /></label>}
    {data?.benchmark && <Table wrapClassName="overflow-auto" caption="Native model benchmark results" className="w-full min-w-[640px] text-sm"><THead><tr><Th>Tier</Th><Th>Rubric</Th><Th>Agreement</Th><Th>Cost USD</Th><Th>Seconds</Th><Th>Missing verifier</Th><Th>Protocol errors</Th></tr></THead><tbody>{data.benchmark.rows.map((row, index) => <tr key={index}><Td>{row.tier}</Td><Td>{row.rubric_class}</Td><Td>{row.agreement ?? 'Unknown'}</Td><Td>{row.cost_usd ?? 'Unknown'}</Td><Td>{row.wall_secs ?? 'Unknown'}</Td><Td>{row.verifier_absent}</Td><Td>{row.protocol_errors}</Td></tr>)}</tbody></Table>}
  </section>
}
