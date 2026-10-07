import { useState } from 'react'
import { RefreshCw, ShieldAlert } from 'lucide-react'
import { api, type HypermidSecurityStatusWire } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { confirmDestructive } from '../../shared/ui/dialog'
import { Surface } from '../../shared/ui/Surface'
import { PanelHeader, Section } from '../settings/settingsUI'
import { NetworkGrants } from './NetworkGrants'
import { SecurityRecovery } from './SecurityRecovery'
import { UnknownEffects } from './UnknownEffects'

export function SecurityView({ status, busy = '', error = '', onRefresh, onRevoke }: {
  status: HypermidSecurityStatusWire
  busy?: string
  error?: string
  onRefresh: () => void
  onRevoke: (grantId: string) => void
}) {
  return <div>
    <PanelHeader title="Network security" hint="Inspect scoped outbound grants and recent policy decisions without exposing credentials or request content." />
    <Section right={<Button size="sm" variant="secondary" className="hypermid-touch" onClick={onRefresh}><RefreshCw size={14} /> Refresh</Button>}>
      {status.availability === 'ready'
        ? <NetworkGrants grants={status.grants} decisions={status.decisions} onRevoke={onRevoke} />
        : <Surface tone="container" radius="lg" className="p-l">
          <div className="flex items-start gap-s"><ShieldAlert size={18} aria-hidden className="mt-0.5 shrink-0 text-warn" />
            <div><h2 data-type="title-m" className="font-medium text-on-surface">Network security status unavailable</h2>
              <p data-type="body-s" className="mt-xs break-words text-on-surface-low">{status.error?.message || 'The authenticated network policy authority did not return a status.'}</p></div>
          </div>
        </Surface>}
      {busy && <p role="status" aria-live="polite" className="sr-only">Updating network grant</p>}
      {error && <p data-type="body-s" role="alert" aria-live="assertive" className="mt-m break-words text-danger">{error}</p>}
    </Section>
  </div>
}

export function Security() {
  const query = useQuery('hypermid:security:policy', () => api.hypermidSecurityPolicy(), { staleAfterMs: 5_000 })
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  if (!query.data && query.error) return <LoadError what="Hypermid network security" error={query.error} onRetry={query.refresh} />
  if (!query.data) return <FormSkeleton sections={1} rows={3} what="Hypermid network security" />

  const status = query.data
  const revoke = async (grantId: string) => {
    const grant = status.grants.find(candidate => candidate.grantId === grantId)
    if (!grant) { setError('This network grant is no longer available. Refresh and try again.'); return }
    if (!(await confirmDestructive(
      `Revoke network access to ${grant.scheme}://${grant.hostname}?`,
      'Future requests covered by this grant will be denied. Active effects remain subject to their authoritative outcome state.',
      { confirmLabel: 'Revoke grant' },
    ))) return
    setBusy(grantId); setError('')
    try {
      await api.revokeHypermidNetworkGrant(grantId)
      await query.refresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The network grant could not be revoked.')
    } finally { setBusy('') }
  }

  return <>
    <SecurityView status={query.data} busy={busy} error={error} onRefresh={query.refresh} onRevoke={(grantId) => void revoke(grantId)} />
    <UnknownEffects />
    <SecurityRecovery />
  </>
}
