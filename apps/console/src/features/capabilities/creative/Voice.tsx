import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Config = { revision: number; baseline: string; min_words: number; min_chapters: number; z_threshold: number; wells: Record<string, string[]> }
type Row = { chapter_id: string; title: string; work_id: string | null; draft_id: string | null; artifact_id?: string; artifact_version?: number; gate: string | null; fingerprint: { words: number; metrics: Record<string, number> } | null }
type Report = { series_revision: number; formula_version: string; method: string; config: Config; rows: Row[]; metric_labels: Record<string, string>; gate: string | null; baseline: Record<string, { center: number | null; std: number | null }>; samples: { artifact_id: string; artifact_version: number; title: string; missing: boolean }[]; findings: { chapter_id: string | null; metric: string; direction: string; value: number; center: number; kind: string }[] }
const control = 'w-full rounded border border-outline bg-surface p-2 text-on-surface'
export default function Voice({ id, revision, apiRoot, sourceKey = "" }: { id: string; revision: number; apiRoot: string; sourceKey?: string }) {
  const [report, setReport] = useState<Report | null>(null)
  const [config, setConfig] = useState<Config | null>(null)
  const [wells, setWells] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [exported, setExported] = useState('')
  const root = `${apiRoot}/${id}/voice`
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'Voice diagnostics failed')
  function apply(result: Report) { setReport(result); setConfig(result.config); setWells(Object.entries(result.config.wells).map(([name, terms]) => `${name}: ${terms.join(', ')}`).join('\n')) }
  useEffect(() => {
    let alive = true; setReport(null); setConfig(null); setError(''); setExported('')
    requestJson<Report>(root).then(result => { if (alive) apply(result) }).catch(e => { if (alive) fail(e) })
    return () => { alive = false }
  }, [root, revision, refresh, sourceKey])
  async function save() {
    if (!config || !report) return
    setBusy(true); setError('')
    try {
      const groups: Record<string, string[]> = {}
      for (const line of wells.split('\n').filter(v => v.trim())) {
        const split = line.indexOf(':'); const name = line.slice(0, split).trim()
        if (split < 1 || Object.hasOwn(groups, name)) throw new Error('Use one unique vocabulary group per line: name: word, word')
        groups[name] = line.slice(split + 1).split(',').map(v => v.trim())
      }
      await requestJson(root, 'PATCH', { ...config, wells: groups, series_revision: report.series_revision })
      apply(await requestJson<Report>(root))
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function exportReport() { try { setExported(JSON.stringify(await requestJson(`${root}/export`), null, 2)) } catch (e) { fail(e) } }
  return <section aria-label="Voice fingerprint diagnostics" className="space-y-3 rounded border border-outline p-3"><h2>Voice fingerprint diagnostics</h2>
    {error && <p role="alert">{error}</p>}{!report && !error && <p>Loading voice diagnostics…</p>}<Button disabled={busy} onClick={() => setRefresh(v => v + 1)}>Refresh voice diagnostics</Button>
    {report && config && <><p>{report.method}</p><p>Formula {report.formula_version} · configuration revision {config.revision}</p>
      <label className="block">Voice baseline<select className={control} value={config.baseline} onChange={e => setConfig({ ...config, baseline: e.target.value })}><option value="drafted">Drafted chapters</option><option value="exemplars">Pinned author samples</option><option value="blended">Blended chapters and samples</option></select></label>
      <label className="block">Minimum words<input className={control} type="number" value={config.min_words} onChange={e => setConfig({ ...config, min_words: Number(e.target.value) })} /></label>
      <label className="block">Minimum chapters<input className={control} type="number" value={config.min_chapters} onChange={e => setConfig({ ...config, min_chapters: Number(e.target.value) })} /></label>
      <label className="block">Drift threshold<input className={control} type="number" step="0.1" value={config.z_threshold} onChange={e => setConfig({ ...config, z_threshold: Number(e.target.value) })} /></label>
      <label className="block">Vocabulary groups<textarea className={control} placeholder="craft: forge, anvil" value={wells} onChange={e => setWells(e.target.value)} /></label>
      <Button disabled={busy} onClick={() => void save()}>Save voice configuration</Button>
      {report.gate && <p role="status">Drift unavailable: {report.gate === 'below_min_chapters' ? 'Too few eligible chapters' : 'Pinned author samples are missing or too short'}</p>}
      <section aria-label="Voice sources"><h3>Voice sources</h3>{report.rows.map(row => <p key={row.chapter_id}>{row.title}: {row.fingerprint?.words ?? 'Unknown'} words{row.gate && ` · ${row.gate}`}{row.work_id && <a href={`#/capabilities/creative?view=works&work=${row.work_id}`}> Open writing work</a>}{row.artifact_id && <a href={`/api/artifacts/${encodeURIComponent(row.artifact_id)}/raw?version=${row.artifact_version}`} target="_blank" rel="noreferrer"> Pinned draft version {row.artifact_version}</a>}</p>)}{report.samples.map(sample => <p key={`${sample.artifact_id}:${sample.artifact_version}`}>{sample.title} · version {sample.artifact_version}{sample.missing && ' · Source missing'}</p>)}</section>
      <div className="overflow-x-auto"><table aria-label="Voice metrics matrix"><thead><tr><th>Metric</th><th>Baseline center</th><th>Population spread</th>{report.rows.map(row => <th key={row.chapter_id}>{row.title}</th>)}</tr></thead><tbody>{Object.entries(report.metric_labels).map(([key, label]) => <tr key={key}><th>{label}</th><td>{report.baseline[key].center ?? 'Unavailable'}</td><td>{report.baseline[key].std ?? 'Unavailable'}</td>{report.rows.map(row => <td key={row.chapter_id}>{row.fingerprint ? row.fingerprint.metrics[key] : 'Unavailable'}</td>)}</tr>)}</tbody></table></div>
      <section aria-label="Voice drift findings"><h3>Voice drift findings</h3>{!report.gate && !report.findings.length && <p>No threshold crossings.</p>}{report.findings.map((finding, index) => <p key={index}>{finding.chapter_id ? report.rows.find(row => row.chapter_id === finding.chapter_id)?.title : 'All eligible chapters'} · {report.metric_labels[finding.metric]} · {finding.direction} ({finding.value}; baseline {finding.center})</p>)}</section>
      <Button onClick={() => void exportReport()}>Export voice diagnostics</Button>{exported && <label className="block">Voice diagnostics export<textarea className={control} readOnly value={exported} /></label>}
    </>}
  </section>
}
