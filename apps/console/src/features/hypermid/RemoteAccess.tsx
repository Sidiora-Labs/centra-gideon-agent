import { useState } from 'react'
import { Globe2, RefreshCw, ShieldCheck, Smartphone, Trash2 } from 'lucide-react'
import { api, type HypermidRemotePlanWire } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { EmptyState, FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { confirm } from '../../shared/ui/dialog'
import { TextInput } from '../../shared/ui/forms'
import { Surface } from '../../shared/ui/Surface'
import { StatusPill } from '../settings/bento'
import { Row, RowGroup, Section } from '../settings/settingsUI'

export const REMOTE_CAPABILITIES = [
  { id: 'sessions.inspect', label: 'Inspect sessions' },
  { id: 'memory.list', label: 'List memory' },
  { id: 'memory.inspect', label: 'Inspect memory evidence' },
  { id: 'cache.list', label: 'Inspect caches' },
  { id: 'config.read', label: 'Read configuration' },
  { id: 'maintenance.status', label: 'View maintenance status' },
] as const

export function validateRemoteEndpoint(value: string): string {
  try {
    const endpoint = new URL(value)
    if (endpoint.protocol !== 'tcp:') return 'Use an explicit TCP endpoint. Hypermid protects it with TLS and never downgrades to plaintext.'
    if (endpoint.username || endpoint.password) return 'Do not place credentials in the endpoint URL.'
    if (!endpoint.hostname || !endpoint.port) return 'Enter the remote server hostname and port.'
    return ''
  } catch { return 'Enter a complete HTTPS endpoint.' }
}

export function RemoteAccess() {
  const status = useQuery('hypermid:remote:status', () => api.hypermidRemoteStatus(), { staleAfterMs: 5_000 })
  const [endpoint, setEndpoint] = useState('')
  const [serverName, setServerName] = useState('')
  const [deviceName, setDeviceName] = useState('')
  const [expiryHours, setExpiryHours] = useState('24')
  const [capabilities, setCapabilities] = useState<string[]>(['sessions.inspect', 'memory.list', 'memory.inspect', 'cache.list'])
  const [plan, setPlan] = useState<HypermidRemotePlanWire>()
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const endpointError = endpoint ? validateRemoteEndpoint(endpoint) : ''
  if (!status.data && status.error) return <LoadError what="Hypermid remote access" error={status.error} onRetry={status.refresh} />
  if (!status.data) return <FormSkeleton sections={2} rows={3} what="Hypermid remote access" />

  const toggleCapability = (id: string, enabled: boolean) => setCapabilities((current) => enabled ? [...new Set([...current, id])] : current.filter((item) => item !== id))
  const review = async () => {
    const problem = validateRemoteEndpoint(endpoint)
    if (problem) { setError(problem); return }
    setBusy('plan'); setError(''); setPlan(undefined)
    try {
      setPlan(await api.planHypermidRemote({
        endpoint,
        server_name: serverName.trim(),
        device_name: deviceName.trim(),
        expires_at: Date.now() + Number(expiryHours) * 3_600_000,
        capabilities,
      }))
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Remote access review was refused.') }
    finally { setBusy('') }
  }
  const enable = async () => {
    if (!plan) return
    setBusy('enable'); setError('')
    try { await api.enableHypermidRemote(plan.plan_digest); setPlan(undefined); status.refresh() }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Remote access was not enabled.') }
    finally { setBusy('') }
  }
  const revoke = async (deviceId: string, displayName: string) => {
    if (!(await confirm({ title: `Revoke ${displayName}?`, body: 'The device will be unable to start a new Hypermid session.', confirmLabel: 'Revoke device', danger: true }))) return
    setBusy(`revoke:${deviceId}`); setError('')
    try { await api.revokeHypermidRemoteDevice(deviceId); status.refresh() }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'The device could not be revoked.') }
    finally { setBusy('') }
  }

  return <div className="hypermid-remote">
    <div className="mb-l"><h1 data-type="title-l" className="text-on-surface">Remote access</h1>
      <p data-type="body-s" className="mt-xs text-on-surface-low">Opt in with TLS, a named device, a short expiry, and the smallest capability set needed.</p></div>
    <Section title="Connection state" right={<Button size="sm" variant="secondary" onClick={status.refresh}><RefreshCw size={14} /> Refresh</Button>}>
      <Surface tone="container" radius="lg" className="p-l">
        <div className="flex flex-wrap items-center gap-s"><Globe2 size={17} className="text-primary" />
          <StatusPill label={status.data.state} tone={status.data.state === 'remote' ? 'ok' : status.data.state === 'degraded' ? 'warn' : 'muted'} />
          <StatusPill label={status.data.tls.configured ? 'TLS configured' : 'TLS required'} tone={status.data.tls.configured ? 'ok' : 'warn'} />
        </div>
        <p className="mt-s text-sm text-on-surface-low">{status.data.detail || (status.data.enabled ? 'Remote access is explicitly enabled.' : 'Remote access is disabled; local operation continues.')}</p>
      </Surface>
    </Section>
    <Section title="Enroll a device" hint="Review creates an authoritative plan. Enablement requires that exact current plan digest.">
      <RowGroup>
        <Row label="TLS endpoint" hint={endpointError || 'Explicit host and port. TLS is mandatory; credentials and private keys do not belong here.'}><TextInput value={endpoint} onChange={setEndpoint} ariaLabel="TLS endpoint" placeholder="tcp://hypermid.example.net:443" size="sm" /></Row>
        <Row label="Server name" hint="The certificate name this device must verify."><TextInput value={serverName} onChange={setServerName} ariaLabel="TLS server name" placeholder="hypermid.example.net" size="sm" /></Row>
        <Row label="Device name" hint="A human-readable name you will recognize when revoking access."><TextInput value={deviceName} onChange={setDeviceName} ariaLabel="Device name" placeholder="My phone" size="sm" /></Row>
        <Row label="Expires after"><select value={expiryHours} onChange={(event) => setExpiryHours(event.target.value)} aria-label="Enrollment expiry"
          className="min-h-11 rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary">
          <option value="1">1 hour</option><option value="24">1 day</option><option value="168">7 days</option>
        </select></Row>
      </RowGroup>
      <fieldset className="mt-m"><legend className="mb-s text-sm text-on-surface">Allowed capabilities</legend>
        <div className="grid gap-s sm:grid-cols-2">{REMOTE_CAPABILITIES.map((capability) => <label key={capability.id} className="hypermid-touch flex cursor-pointer items-center gap-s rounded-lg bg-surface-container px-m text-sm text-on-surface">
          <input type="checkbox" checked={capabilities.includes(capability.id)} onChange={(event) => toggleCapability(capability.id, event.target.checked)} className="size-4 accent-primary" />{capability.label}
        </label>)}</div>
      </fieldset>
      <div className="mt-m flex justify-end"><Button size="sm" disabled={!endpoint || !serverName.trim() || !deviceName.trim() || !capabilities.length || Boolean(endpointError)} disabledReason={!endpoint ? 'Enter the remote endpoint.' : endpointError || (!serverName.trim() ? 'Enter a server name.' : !deviceName.trim() ? 'Enter a device name.' : !capabilities.length ? 'Select at least one read capability.' : undefined)} loading={busy === 'plan'} onClick={() => void review()}><ShieldCheck size={14} /> Review remote access</Button></div>
      {plan && <div role="dialog" aria-modal="false" aria-labelledby="remote-plan-title" className="hypermid-review mt-m rounded-lg border border-outline-variant bg-surface p-l">
        <h3 id="remote-plan-title" className="text-base text-on-surface">Review device enrollment</h3>
        <dl className="mt-m grid gap-s text-sm sm:grid-cols-2"><div><dt className="text-on-surface-low">Device</dt><dd className="text-on-surface">{plan.device_name}</dd></div>
          <div><dt className="text-on-surface-low">Scope</dt><dd className="text-on-surface">{plan.scope_label}</dd></div><div><dt className="text-on-surface-low">TLS server</dt><dd className="break-words text-on-surface">{plan.server_name}</dd></div>
          <div><dt className="text-on-surface-low">Expires</dt><dd className="text-on-surface">{new Date(plan.expires_at).toLocaleString()}</dd></div></dl>
        <p className="mt-m text-sm text-on-surface-low">Capabilities: {plan.capabilities.map((id) => REMOTE_CAPABILITIES.find((item) => item.id === id)?.label || id).join(', ')}.</p>
        {plan.warnings.map((warning) => <p key={warning} role="alert" className="mt-s text-sm text-warn">{warning}</p>)}
        <div className="hypermid-action-bar mt-m flex flex-wrap justify-end gap-s"><Button size="sm" variant="secondary" onClick={() => setPlan(undefined)}>Cancel</Button>
          <Button size="sm" loading={busy === 'enable'} onClick={() => void enable()}>Enable with reviewed plan</Button></div>
      </div>}
    </Section>
    <Section title="Enrolled devices" hint="Revocation blocks a fresh session. Existing terminal receipts remain authoritative.">
      {status.data.devices.length === 0 ? <EmptyState icon={Smartphone} title="No enrolled devices" hint="Remote access stays unavailable until a device is enrolled." />
        : <div className="grid gap-m">{status.data.devices.map((device) => <Surface key={device.device_id} tone="container" radius="lg" className="p-l">
          <div className="flex flex-wrap items-start justify-between gap-m"><div><div className="flex items-center gap-s"><Smartphone size={16} className="text-primary" />
            <h3 className="text-sm text-on-surface">{device.name}</h3><StatusPill label={device.state} tone={device.state === 'active' ? 'ok' : device.state === 'revoked' ? 'warn' : 'muted'} /></div>
            <p data-type="caption" className="mt-xs text-on-surface-low">Expires {new Date(device.expires_at).toLocaleString()} · {device.capabilities.length} capabilities</p></div>
            {device.state === 'active' && <Button size="sm" variant="danger" loading={busy === `revoke:${device.device_id}`} onClick={() => void revoke(device.device_id, device.name)}><Trash2 size={14} /> Revoke</Button>}</div>
        </Surface>)}</div>}
    </Section>
    {error && <p role="alert" aria-live="assertive" className="text-sm text-danger">{error}</p>}
  </div>
}
