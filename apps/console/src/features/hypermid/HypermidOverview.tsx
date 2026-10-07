import { Activity, Database, RefreshCw, Server, Waypoints } from 'lucide-react'
import { api } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { EmptyState, LoadError, FormSkeleton } from '../../shared/ui/ListScaffold'
import { Surface } from '../../shared/ui/Surface'
import { StatusPill } from '../settings/bento'
import { PanelHeader, Section } from '../settings/settingsUI'
import { CacheInspector } from './CacheInspector'
import { MemoryInspector } from './MemoryInspector'
import { SessionInspector } from './SessionInspector'

function readableMode(mode: string): string {
  return mode === 'pass_through' ? 'Pass-through' : mode.charAt(0).toUpperCase() + mode.slice(1)
}

function StatusCard({ icon: Icon, label, value, detail }: {
  icon: typeof Server
  label: string
  value: string
  detail?: string
}) {
  const tone = ['ready', 'healthy', 'available'].includes(value) ? 'ok'
    : ['degraded', 'starting', 'draining'].includes(value) ? 'warn' : 'muted'
  return <Surface tone="container" radius="lg" className="min-w-0 p-l">
    <div className="mb-s flex items-center gap-s text-on-surface-low"><Icon size={16} aria-hidden /><span data-type="label-s">{label}</span></div>
    <StatusPill label={value.replaceAll('_', ' ')} tone={tone} />
    {detail && <p data-type="caption" className="mt-s text-on-surface-low">{detail}</p>}
  </Surface>
}

export function HypermidOverview({ onConfigure }: { onConfigure?: () => void }) {
  const overview = useQuery('hypermid:overview', () => api.hypermidOverview(), { staleAfterMs: 5_000 })
  if (!overview.data && overview.error) return <LoadError what="Hypermid status" error={overview.error} onRetry={overview.refresh} />
  if (!overview.data) return <FormSkeleton sections={3} rows={2} what="Hypermid status" />
  const data = overview.data
  const scope = data.scope.workspace_id ? 'This workspace' : 'This project'
  const knownOff = !overview.error && data.mode === 'off' && data.availability === 'unavailable'
    && (data.daemon.state === 'stopped' || data.daemon.state === 'absent')
    && data.adapter.availability === 'unavailable' && !data.adapter.full_host_integration
    && (data.failure_code == null || data.failure_code === 'NOT_CONFIGURED')
  return <div>
    <PanelHeader title="Hypermid" hint="Inspect the context, memory, and cache state Gideon is using for this project." />
    <Section title="Current state" right={<Button size="sm" variant="secondary" onClick={overview.refresh}><RefreshCw size={14} /> Refresh</Button>}>
      <div className="grid gap-m sm:grid-cols-2 xl:grid-cols-4">
        <StatusCard icon={Server} label="Daemon" value={data.daemon.state} detail={data.daemon.detail} />
        <StatusCard icon={Waypoints} label="Host integration" value={data.adapter.availability}
          detail={data.adapter.full_host_integration ? 'Full Gideon integration' : data.adapter.detail || 'Limited integration'} />
        <StatusCard icon={Database} label="Store" value={data.store_health} detail={scope} />
        <StatusCard icon={Activity} label="Runtime mode" value={readableMode(data.mode)}
          detail={data.cursor ? `Cursor ${data.cursor.epoch}:${data.cursor.sequence}` : 'No cursor observed'} />
      </div>
      <p data-type="caption" className="mt-m break-words text-on-surface-low">
        Build {data.versions.build || 'unknown'} · Protocol {data.versions.protocol || 'unknown'} · Storage {data.versions.storage || 'unknown'}
        {data.checked_at_ms > 0 ? ` · checked ${new Date(data.checked_at_ms).toLocaleString()}` : ''}
      </p>
      {data.maintenance.active && <p data-type="body-s" role="status" className="mt-m rounded-lg bg-warn/10 px-m py-s text-on-surface">
        Maintenance is running{data.maintenance.label ? `: ${data.maintenance.label}` : ''}.
      </p>}
    </Section>
    {knownOff ? <EmptyState icon={Database} title="Hypermid is off"
      hint="Session, memory, and cache inspection requires a connected Hypermid runtime with an authenticated project scope. Configure Hypermid before inspecting its state."
      action={onConfigure ? { label: 'Configure Hypermid', onClick: onConfigure } : undefined} />
      : <><SessionInspector /><MemoryInspector /><CacheInspector /></>}
  </div>
}
