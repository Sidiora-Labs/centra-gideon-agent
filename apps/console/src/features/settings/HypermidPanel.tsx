import { useState } from 'react'
import { HypermidOverview } from '../hypermid/HypermidOverview'
import { CredentialVault } from '../hypermid/CredentialVault'
import { ModelBindings } from '../hypermid/ModelBindings'
import { RuntimeConfig } from '../hypermid/RuntimeConfig'
import { RemoteAccess } from '../hypermid/RemoteAccess'
import { Diagnostics } from '../hypermid/Diagnostics'
import { LiveLogs } from '../hypermid/LiveLogs'
import { Operations } from '../hypermid/Operations'
import { Lifecycle } from '../hypermid/Lifecycle'
import { Connections } from '../hypermid/Connections'
import { Security } from '../hypermid/Security'
import '../hypermid/hypermid.css'

export function HypermidPanel() {
  const [view, setView] = useState<'inspect' | 'configure' | 'connections' | 'security' | 'operations' | 'lifecycle' | 'diagnostics' | 'remote'>('inspect')
  return <div className="hypermid-surface">
    <div role="tablist" aria-label="Hypermid area" className="mb-xl inline-flex max-w-full flex-wrap rounded-2xl bg-surface-container p-1">
      {([['inspect', 'Inspect'], ['configure', 'Configure'], ['connections', 'Connections'], ['security', 'Security'], ['operations', 'Operations'], ['lifecycle', 'Lifecycle'], ['diagnostics', 'Health & logs'], ['remote', 'Remote access']] as const).map(([id, label]) => <button key={id} type="button" role="tab"
        aria-selected={view === id} onClick={() => setView(id)}
        className="min-h-11 rounded-pill px-m text-sm text-on-surface-low aria-selected:bg-surface-highest aria-selected:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
        {label}
      </button>)}
    </div>
    {view === 'inspect' ? <HypermidOverview /> : view === 'remote' ? <RemoteAccess /> : view === 'connections' ? <Connections /> : view === 'security' ? <Security /> : view === 'operations' ? <Operations /> : view === 'lifecycle' ? <Lifecycle /> : view === 'diagnostics' ? <div>
      <div className="mb-l"><h1 data-type="title-l" className="text-on-surface">Hypermid health</h1>
        <p data-type="body-s" className="mt-xs text-on-surface-low">Review observed checks and bounded redacted daemon logs for this scope.</p></div>
      <Diagnostics />
      <LiveLogs />
    </div> : <div>
      <div className="mb-l"><h1 data-type="title-l" className="text-on-surface">Configure Hypermid</h1>
        <p data-type="body-s" className="mt-xs text-on-surface-low">Manage scoped runtime policy, model duties, and write-only credentials.</p></div>
      <RuntimeConfig />
      <ModelBindings />
      <CredentialVault />
    </div>}
  </div>
}
