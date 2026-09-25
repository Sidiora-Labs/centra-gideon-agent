import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Usage = { bytes: number; files: number; directories: number; entries_scanned: number; skipped_symlinks: number; skipped_mounts: number; complete: boolean; partial_reasons: string[] }
type ProjectUsage = { project_id: string; name: string; workspace: string; workspace_usage: Usage; metadata_usage: Usage | null }
type Report = { generated_at: string; entry_limit: number; runtime_usage: Usage; projects: ProjectUsage[] }
const endpoint = '/api/capabilities/workspace/storage'

function Size({ value }: { value: number }) {
  return <>{new Intl.NumberFormat(undefined, { style: 'unit', unit: 'byte', notation: 'compact', unitDisplay: 'narrow' }).format(value)}</>
}

function Summary({ label, usage }: { label: string; usage: Usage }) {
  return <div><strong>{label}: <Size value={usage.bytes} /></strong><span> · {usage.files} files · {usage.directories} directories</span>
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
  return <section aria-label="Storage diagnosis" className="space-y-3 border-t border-outline pt-4">
    <h2 className="text-lg font-semibold">Storage diagnosis</h2>
    <p>Read-only usage for Gideon's owned state and registered project roots. Linked targets and mounted filesystems are excluded.</p>
    <Button loading={busy} onClick={() => void load()}>Refresh storage usage</Button>
    {error && <p role="alert">{error}</p>}
    {!report && !error && <p role="status">Scanning owned storage…</p>}
    {report && <div className="space-y-2"><Summary label="Gideon runtime" usage={report.runtime_usage} />
      {report.projects.length === 0 && <p>No registered project storage.</p>}
      {report.projects.map(project => <article key={project.project_id} className="break-words"><h3>{project.name}</h3><p>{project.workspace}</p><Summary label="Workspace" usage={project.workspace_usage} />{project.metadata_usage && <Summary label="Project metadata" usage={project.metadata_usage} />}</article>)}
      <p>Measured {new Date(report.generated_at).toLocaleString()} · limit {report.entry_limit} entries per root.</p>
    </div>}
  </section>
}
