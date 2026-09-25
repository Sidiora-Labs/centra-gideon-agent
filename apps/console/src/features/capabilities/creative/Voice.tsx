import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Surface } from '../../../shared/ui/Surface'
import { Field, NumberField, Select, TextArea } from '../../../shared/ui/forms'

type Config = { revision: number; baseline: string; min_words: number; min_chapters: number; z_threshold: number; wells: Record<string, string[]> }
type Row = { chapter_id: string; title: string; work_id: string | null; draft_id: string | null; artifact_id?: string; artifact_version?: number; gate: string | null; fingerprint: { words: number; metrics: Record<string, number> } | null }
type Report = { series_revision: number; formula_version: string; method: string; config: Config; rows: Row[]; metric_labels: Record<string, string>; gate: string | null; baseline: Record<string, { center: number | null; std: number | null }>; samples: { artifact_id: string; artifact_version: number; title: string; missing: boolean }[]; findings: { chapter_id: string | null; metric: string; direction: string; value: number; center: number; kind: string }[] }
export default function Voice({ id, revision, apiRoot, sourceKey = "" }: { id: string; revision: number; apiRoot: string; sourceKey?: string }) {
  const t = (value: string) => value
  const [report, setReport] = useState<Report | null>(null)
  const [config, setConfig] = useState<Config | null>(null)
  const [wells, setWells] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [exported, setExported] = useState('')
  const root = `${apiRoot}/${id}/voice`
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : t('Voice diagnostics failed'))
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
        if (split < 1 || Object.hasOwn(groups, name)) throw new Error(t('Use one unique vocabulary group per line: name: word, word'))
        groups[name] = line.slice(split + 1).split(',').map(v => v.trim())
      }
      await requestJson(root, 'PATCH', { ...config, wells: groups, series_revision: report.series_revision })
      apply(await requestJson<Report>(root))
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function exportReport() { try { setExported(JSON.stringify(await requestJson(`${root}/export`), null, 2)) } catch (e) { fail(e) } }
  return <Surface className="p-l"><section aria-label={t('Voice fingerprint diagnostics')} className="space-y-m"><div><h2 data-type="title-m" className="text-on-surface">{t('Voice fingerprint diagnostics')}</h2><p data-type="body-m" className="text-on-surface-low">{t('Compare chapter voice against the selected baseline before changing thresholds or vocabulary groups.')}</p></div>
    {error && <p role="alert">{error}</p>}{!report && !error && <p>{t('Loading voice diagnostics…')}</p>}<Button disabled={busy} onClick={() => setRefresh(v => v + 1)}>{t('Refresh voice diagnostics')}</Button>
    {report && config && <><p>{t(report.method)}</p><p>{t('Formula')} {report.formula_version} · {t('configuration revision')} {config.revision}</p>
      <div className="grid gap-m sm:grid-cols-2"><Field label={t('Voice baseline')}><Select value={config.baseline} onChange={baseline => setConfig({ ...config, baseline })} options={[{ value: 'drafted', label: t('Drafted chapters') }, { value: 'exemplars', label: t('Pinned author samples') }, { value: 'blended', label: t('Blended chapters and samples') }]} /></Field>
      <Field label={t('Minimum words')}><NumberField value={config.min_words} min={1} width="w-full" onChange={min_words => setConfig({ ...config, min_words })} /></Field>
      <Field label={t('Minimum chapters')}><NumberField value={config.min_chapters} min={1} width="w-full" onChange={min_chapters => setConfig({ ...config, min_chapters })} /></Field>
      <Field label={t('Drift threshold')}><NumberField value={config.z_threshold} min={0} step={0.1} width="w-full" onChange={z_threshold => setConfig({ ...config, z_threshold })} /></Field></div>
      <Field label={t('Vocabulary groups')}><TextArea placeholder={t('craft: forge, anvil')} value={wells} onChange={setWells} rows={6} /></Field>
      <Button disabled={busy} onClick={() => void save()}>{t('Save voice configuration')}</Button>
      {report.gate && <p role="status">{t('Drift unavailable:')} {report.gate === 'below_min_chapters' ? t('Too few eligible chapters') : t('Pinned author samples are missing or too short')}</p>}
      <section aria-label={t('Voice sources')} className="space-y-s"><h3 data-type="title-s">{t('Voice sources')}</h3>{report.rows.map(row => <p key={row.chapter_id}>{row.title}: {row.fingerprint?.words ?? t('Unknown')} {t('words')}{row.gate && ` · ${t(({source_missing:'Source missing',not_drafted:'Not drafted',below_min_words:'Below minimum words'} as Record<string,string>)[row.gate] || row.gate)}`}{row.work_id && <a href={`#/capabilities/creative?view=works&work=${row.work_id}`}> {t('Open writing work')}</a>}{row.artifact_id && <a href={`/api/artifacts/${encodeURIComponent(row.artifact_id)}/raw?version=${row.artifact_version}`} target="_blank" rel="noreferrer"> {t('Pinned draft version')} {row.artifact_version}</a>}</p>)}{report.samples.map(sample => <p key={`${sample.artifact_id}:${sample.artifact_version}`}>{sample.title} · {t('version')} {sample.artifact_version}{sample.missing && ` · ${t('Source missing')}`}</p>)}</section>
      <div className="overflow-x-auto rounded-lg border border-outline-variant/30"><table className="w-full text-left [&_td]:border-t [&_td]:border-outline-variant/20 [&_td]:p-m [&_th]:p-m" aria-label={t('Voice metrics matrix')}><thead><tr><th>{t('Metric')}</th><th>{t('Baseline center')}</th><th>{t('Population spread')}</th>{report.rows.map(row => <th key={row.chapter_id}>{row.title}</th>)}</tr></thead><tbody>{Object.entries(report.metric_labels).map(([key, label]) => <tr key={key}><th>{key.startsWith('well:') ? `${t('Vocabulary:')} ${key.slice(5)} ${t('per 1000 words')}` : t(label)}</th><td>{report.baseline[key].center ?? t('Unavailable')}</td><td>{report.baseline[key].std ?? t('Unavailable')}</td>{report.rows.map(row => <td key={row.chapter_id}>{row.fingerprint ? row.fingerprint.metrics[key] : t('Unavailable')}</td>)}</tr>)}</tbody></table></div>
      <section aria-label={t('Voice drift findings')} className="space-y-s"><h3 data-type="title-s">{t('Voice drift findings')}</h3>{!report.gate && !report.findings.length && <p>{t('No threshold crossings.')}</p>}{report.findings.map((finding, index) => <p key={index}>{finding.chapter_id ? report.rows.find(row => row.chapter_id === finding.chapter_id)?.title : t('All eligible chapters')} · {finding.metric.startsWith('well:') ? `${t('Vocabulary:')} ${finding.metric.slice(5)} ${t('per 1000 words')}` : t(report.metric_labels[finding.metric])} · {t(finding.direction)} ({finding.value}; {t('baseline')} {finding.center})</p>)}</section>
      <Button onClick={() => void exportReport()}>{t('Export voice diagnostics')}</Button>{exported && <Field label={t('Voice diagnostics export')}><TextArea disabled value={exported} onChange={() => undefined} rows={8} mono /></Field>}
    </>}
  </section></Surface>
}
