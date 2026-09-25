import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Surface } from '../../../shared/ui/Surface'

type Usage = { bytes: number; files: number; directories: number; entries_scanned: number; skipped_symlinks: number; skipped_mounts: number; complete: boolean; partial_reasons: string[] }
type ProjectUsage = { project_id: string; name: string; workspace: string; workspace_usage: Usage; metadata_usage: Usage | null }
type Report = { generated_at: string; entry_limit: number; runtime_usage: Usage; projects: ProjectUsage[] }
const endpoint = '/api/capabilities/workspace/storage'

function Size({ value }: { value: number }) {
  return <>{new Intl.NumberFormat(undefined, { style: 'unit', unit: 'byte', notation: 'compact', unitDisplay: 'narrow' }).format(value)}</>
}

function Summary({ label, usage }: { label: string; usage: Usage }) {
  return <div className="flex flex-wrap justify-between gap-s py-m"><strong>{label}: <Size value={usage.bytes} /></strong><span className="text-on-surface-low">{usage.files} files · {usage.directories} directories</span>
    {usage.skipped_symlinks > 0 && <span> · {usage.skipped_symlinks} symlinks excluded</span>}
    {!usage.complete && <p role="status">Partial scan: {usage.partial_reasons.join(', ')}</p>}
  </div>
}

export default function Storage() {
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function load() {
    if (busy) return
    setBusy(true); setError('')
    try { setReport(await requestJson<Report>(endpoint)) } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setBusy(false) }
  }
  useEffect(() => { void load() }, [])
  return <section aria-label="Storage diagnosis" className="mx-auto w-full space-y-l px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
    <header><h2 data-type="title-m">Storage diagnosis</h2>
    <p data-type="body-s" className="mt-1 text-on-surface-low">Read-only usage for Gideon's owned state and registered project roots. Linked targets and mounted filesystems are excluded.</p></header>
    <Button loading={busy} onClick={() => void load()}>Refresh storage usage</Button>
    {error && <p role="alert">{error}</p>}
    {!report && !error && <p role="status">Scanning owned storage…</p>}
    {report && <div className="space-y-m"><Surface className="divide-y divide-outline-variant/20 px-l"><Summary label="Gideon runtime" usage={report.runtime_usage} /></Surface>
      {report.projects.length === 0 && <p>No registered project storage.</p>}
      {report.projects.map(project => <article key={project.project_id}><Surface className="break-words px-l"><h3 data-type="title-m" className="pt-l">{project.name}</h3><p data-type="body-s" className="text-on-surface-low">{project.workspace}</p><div className="divide-y divide-outline-variant/20"><Summary label="Workspace" usage={project.workspace_usage} />{project.metadata_usage && <Summary label="Project metadata" usage={project.metadata_usage} />}</div></Surface></article>)}
      <p data-type="caption" className="text-on-surface-low">Measured {new Date(report.generated_at).toLocaleString()} · limit {report.entry_limit} entries per root.</p>
    </div>}
  </section>
}
