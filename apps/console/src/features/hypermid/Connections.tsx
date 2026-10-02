import { useState } from 'react'
import { GitBranch, RefreshCw, Server, TerminalSquare } from 'lucide-react'
import {
  api,
  type HypermidIntegrationConnectionWire,
  type HypermidIntegrationOperation,
  type HypermidIntegrationsWire,
} from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { confirm } from '../../shared/ui/dialog'
import { Surface } from '../../shared/ui/Surface'
import { StatusPill } from '../settings/bento'
import { PanelHeader, Section } from '../settings/settingsUI'

const actionLabels: Record<HypermidIntegrationOperation, string> = {
  enable: 'Enable',
  disable: 'Disable',
  reconnect: 'Reconnect',
  stop: 'Stop',
}

function readable(value: string): string {
  return value.replaceAll('_', ' ').replace(/^./, (character) => character.toUpperCase())
}

function statusTone(state: string): 'ok' | 'warn' | 'muted' {
  if (state === 'ready' || state === 'connected') return 'ok'
  if (state === 'disconnected' || state === 'unavailable') return 'warn'
  return 'muted'
}

function ConnectionCard({ connection, busy, onAction }: {
  connection: HypermidIntegrationConnectionWire
  busy: string
  onAction: (connection: HypermidIntegrationConnectionWire, operation: HypermidIntegrationOperation) => void
}) {
  const Icon = connection.transport === 'stdio' ? TerminalSquare : Server
  const metrics = [
    connection.connected_sessions === null ? null : `${connection.connected_sessions} connected sessions`,
    connection.active_calls === null ? null : `${connection.active_calls} active calls`,
    connection.catalog_generation === null ? null : `catalog generation ${connection.catalog_generation}`,
    connection.process_ready === null ? null : connection.process_ready ? 'process ready' : 'process not ready',
  ].filter((value): value is string => Boolean(value))
  return <Surface tone="container" radius="lg" className="min-w-0 p-l">
    <div className="flex flex-wrap items-start justify-between gap-m">
      <div className="flex min-w-0 items-start gap-s">
        <Icon size={17} aria-hidden className="mt-0.5 shrink-0 text-primary" />
        <div className="min-w-0">
          <h3 className="break-words text-sm font-medium text-on-surface">{connection.display_name}</h3>
          <p data-type="caption" className="mt-xs text-on-surface-low">{readable(connection.transport)} transport</p>
        </div>
      </div>
      <StatusPill label={readable(connection.state)} tone={statusTone(connection.state)} />
    </div>
    <div className="mt-m">
      <p data-type="label-s" className="text-on-surface-low">Negotiated capabilities</p>
      <p className="mt-xs break-words text-sm text-on-surface">
        {connection.capabilities.length ? connection.capabilities.map(readable).join(', ') : 'None reported'}
      </p>
    </div>
    {metrics.length > 0 && <p data-type="caption" className="mt-s break-words text-on-surface-low">{metrics.join(' · ')}</p>}
    {connection.transport === 'stdio' && <p data-type="caption" className="mt-s text-on-surface-low">
      Budget {readable(connection.budget.state)} · fault {connection.fault.code ? readable(connection.fault.code) : readable(connection.fault.state)}
    </p>}
    {connection.fault.code && <p role="status" className="mt-s break-words text-sm text-warn">{readable(connection.fault.code)}</p>}
    {connection.operations.length > 0 && <div className="mt-m flex flex-wrap gap-s">
      {connection.operations.map((operation) => <Button key={operation} size="sm" variant={operation === 'stop' || operation === 'disable' ? 'secondary' : 'tonal'}
        className="hypermid-touch" loading={busy === `${connection.connection_id}:${operation}`}
        onClick={() => onAction(connection, operation)}>{actionLabels[operation]}</Button>)}
    </div>}
  </Surface>
}

export function ConnectionsView({ snapshot, busy = '', error = '', onRefresh, onAction }: {
  snapshot: HypermidIntegrationsWire
  busy?: string
  error?: string
  onRefresh: () => void
  onAction: (connection: HypermidIntegrationConnectionWire, operation: HypermidIntegrationOperation) => void
}) {
  const fullHost = snapshot.connections.filter((connection) => connection.coverage === 'full_host')
  const toolBridges = snapshot.connections.filter((connection) => connection.coverage === 'tool_bridge')
  return <div>
    <PanelHeader title="Connections" hint="Review Hypermid host coverage, bounded tool bridges, supervised processes, and condition observations for this scope." />
    <Section title="Full host integration" hint="Full host coverage requires negotiated identity, scope, sessions, memory, configuration, credentials, lifecycle, cursor resume, and reconnect. Tool availability does not satisfy this status."
      right={<Button size="sm" variant="secondary" className="hypermid-touch" onClick={onRefresh}><RefreshCw size={14} /> Refresh</Button>}>
      <div className="grid gap-m lg:grid-cols-2">
        {fullHost.map((connection) => <ConnectionCard key={connection.connection_id} connection={connection} busy={busy} onAction={onAction} />)}
      </div>
      {fullHost.length === 0 && <p role="status" className="rounded-lg bg-surface-container p-m text-sm text-on-surface-low">No full host integration was reported.</p>}
    </Section>
    <Section title="Tool bridge" hint="Tool bridges expose only their listed catalog and call capabilities. They do not grant configuration, credential, lifecycle, or host-session authority.">
      <div className="grid gap-m lg:grid-cols-2">
        {toolBridges.map((connection) => <ConnectionCard key={connection.connection_id} connection={connection} busy={busy} onAction={onAction} />)}
      </div>
      {toolBridges.length === 0 && <p role="status" className="rounded-lg bg-surface-container p-m text-sm text-on-surface-low">No tool bridge was reported.</p>}
    </Section>
    <Section title="Conditions" hint="Condition results are bounded observations. A transition identity appears only when the native evaluator reports one.">
      <div className="grid gap-m lg:grid-cols-2">{snapshot.conditions.map((condition, index) => <Surface key={condition.condition_id || `${condition.failure_code || 'unavailable'}:${index}`} tone="container" radius="lg" className="p-l">
        <div className="flex flex-wrap items-center justify-between gap-s">
          <div className="flex min-w-0 items-center gap-s"><GitBranch size={16} aria-hidden className="shrink-0 text-primary" />
            <h3 className="break-words text-sm font-medium text-on-surface">{condition.condition_id || 'Condition evaluator'}</h3></div>
          <StatusPill label={condition.availability === 'available' ? condition.result ? 'Matched' : 'Not matched' : 'Unavailable'} tone={condition.availability === 'available' ? condition.result ? 'ok' : 'muted' : 'warn'} />
        </div>
        {condition.transition_id && <p className="mt-s break-all text-xs text-on-surface-low">Transition {condition.transition_id}</p>}
        {condition.failure_code && <p className="mt-s break-words text-sm text-warn">{readable(condition.failure_code)}</p>}
        {condition.observed_facts.length > 0 && <dl className="mt-s grid gap-xs text-sm">{condition.observed_facts.map((fact) => <div key={`${fact.label}:${fact.value}`} className="flex flex-wrap justify-between gap-s">
          <dt className="text-on-surface-low">{fact.label}</dt><dd className="break-all text-on-surface">{fact.value}</dd>
        </div>)}</dl>}
        <p data-type="caption" className="mt-s text-on-surface-low">Checked {new Date(condition.checked_at_ms).toLocaleString()}</p>
      </Surface>)}</div>
    </Section>
    <p data-type="caption" className="mb-m text-on-surface-low">Scope checked {new Date(snapshot.checked_at_ms).toLocaleString()}.</p>
    {error && <p role="alert" aria-live="assertive" className="mb-m break-words text-sm text-danger">{error}</p>}
  </div>
}

export function Connections() {
  const query = useQuery('hypermid:connections', () => api.hypermidConnections(), { staleAfterMs: 5_000 })
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  if (!query.data && query.error) return <LoadError what="Hypermid connections" error={query.error} onRetry={query.refresh} />
  if (!query.data) return <FormSkeleton sections={3} rows={3} what="Hypermid connections" />

  const act = async (connection: HypermidIntegrationConnectionWire, operation: HypermidIntegrationOperation) => {
    if (operation !== 'enable' && !(await confirm({
      title: `${actionLabels[operation]} ${connection.display_name}?`,
      body: operation === 'reconnect' ? 'Active calls may be interrupted while the authenticated connection is re-established.' : 'Active calls on this connection may be interrupted.',
      confirmLabel: actionLabels[operation],
      danger: operation === 'stop' || operation === 'disable',
    }))) return
    const key = `${connection.connection_id}:${operation}`
    setBusy(key); setError('')
    try {
      const result = await api.actHypermidConnection(connection.connection_id, operation)
      if (result.state !== 'applied') setError(`${connection.display_name}: ${readable(result.error_code || result.state)}.`)
      await query.refresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `${connection.display_name} did not accept the operation.`)
    } finally { setBusy('') }
  }

  return <ConnectionsView snapshot={query.data} busy={busy} error={error} onRefresh={query.refresh} onAction={(connection, operation) => void act(connection, operation)} />
}
