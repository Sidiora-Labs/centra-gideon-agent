import { useEffect, useState } from 'react'
import { RefreshCw, Stethoscope } from 'lucide-react'
import { api, type HypermidDiagnosticsWire } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { Surface } from '../../shared/ui/Surface'
import { StatusPill } from '../settings/bento'
import { Section } from '../settings/settingsUI'
import { retainDiagnostics } from './diagnosticsState'

function tone(health: string): 'ok' | 'warn' | 'muted' {
  return health === 'healthy' ? 'ok' : health === 'degraded' || health === 'unhealthy' ? 'warn' : 'muted'
}

export function Diagnostics() {
  const query = useQuery('hypermid:diagnostics', () => api.hypermidDiagnostics(), { staleAfterMs: 15_000 })
  const [retained, setRetained] = useState<HypermidDiagnosticsWire>()
  const [rerunning, setRerunning] = useState(false)
  const [rerunError, setRerunError] = useState('')
  const [announcement, setAnnouncement] = useState('')
  useEffect(() => { setRetained((current) => retainDiagnostics(current, query.data)) }, [query.data])

  const rerun = async () => {
    setRerunning(true); setRerunError(''); setAnnouncement('')
    try {
      const next = await api.hypermidDiagnostics(true)
      setRetained(next)
      setAnnouncement(`Diagnostics updated at ${new Date(next.observed_at).toLocaleString()}.`)
    } catch (caught) {
      setRerunError(caught instanceof Error ? caught.message : 'The diagnostic rerun failed. The previous observation remains visible.')
    } finally { setRerunning(false) }
  }

  const snapshot = retained ?? query.data
  if (!snapshot && query.error) return <LoadError what="Hypermid diagnostics" error={query.error} onRetry={query.refresh} />
  if (!snapshot) return <FormSkeleton sections={2} rows={3} what="Hypermid diagnostics" />
  const scopeLabel = snapshot.scope.workspace_id ? 'This workspace' : 'This project'
  return <Section title="Diagnostics" hint={`${scopeLabel} · ${snapshot.cached ? 'cached observation' : 'fresh observation'} · cursor ${snapshot.cursor.epoch}:${snapshot.cursor.sequence}`}
    right={<Button size="sm" variant="secondary" loading={rerunning} loadingLabel="Running checks" onClick={() => void rerun()}><RefreshCw size={14} /> Run checks</Button>}>
    <div className="grid gap-m lg:grid-cols-2">
      {snapshot.checks.map((check) => <Surface key={check.id} tone="container" radius="lg" className="min-w-0 p-l">
        <div className="flex min-w-0 items-start justify-between gap-m"><div className="flex min-w-0 items-center gap-s">
          <Stethoscope size={16} aria-hidden className="shrink-0 text-primary" />
          <h3 className="min-w-0 break-words text-sm text-on-surface">{check.title}</h3></div>
          <StatusPill label={check.health} tone={tone(check.health)} /></div>
        <p className="mt-s break-words text-sm text-on-surface-low">{check.summary}</p>
        {check.next_action && <p className="mt-s break-words text-sm text-on-surface"><span className="text-on-surface-low">Next action: </span>{check.next_action}</p>}
        <p data-type="caption" className="mt-s text-on-surface-low">Observed {new Date(check.observed_at).toLocaleString()}{check.cached ? ' · cached' : ''}</p>
      </Surface>)}
    </div>
    {snapshot.checks.length === 0 && <p role="status" className="text-sm text-on-surface-low">The daemon returned no diagnostic checks for this scope.</p>}
    {rerunError && <p role="alert" className="mt-m break-words text-sm text-danger">{rerunError} The previous observation remains visible.</p>}
    <p role="status" aria-live="polite" className="sr-only">{announcement}</p>
  </Section>
}
