export type NetworkGrantView = {
  grantId: string
  principalId: string
  operation: string
  scheme: string
  hostname: string
  ports: number[]
  addressClasses: string[]
  proxyPolicy: 'direct' | 'required'
  redirectLimit: number
  byteLimit: number
  expiresAtMs: number
}

export type NetworkDecisionView = {
  code: string
  rule: string
  allowed: boolean
  checkedAtMs: number
}

type Props = {
  grants: NetworkGrantView[]
  decisions?: NetworkDecisionView[]
  onRevoke?: (grantId: string) => void
}

const formatBytes = (bytes: number) => `${bytes.toLocaleString()} bytes`

export function NetworkGrants({ grants, decisions = [], onRevoke }: Props) {
  return <section aria-labelledby="network-grants-title" className="space-y-m">
    <header>
      <h2 id="network-grants-title" className="text-lg font-semibold text-on-surface">Network grants</h2>
      <p className="text-sm text-on-surface-var">Connections are denied unless the destination, resolved addresses, proxy route, and limits match an active grant.</p>
    </header>
    {grants.length === 0 ? <div className="rounded-lg border border-outline-variant bg-surface-container p-m text-sm text-on-surface-var">No network access is granted.</div> : <ul className="space-y-s">
      {grants.map((grant) => <li key={grant.grantId} className="rounded-lg border border-outline-variant bg-surface p-m">
        <div className="flex flex-wrap items-start justify-between gap-s">
          <div>
            <h3 className="font-medium text-on-surface">{grant.scheme}://{grant.hostname}</h3>
            <p className="text-sm text-on-surface-var">{grant.operation} · ports {grant.ports.join(', ')}</p>
          </div>
          {onRevoke && <button type="button" className="rounded-md border border-outline px-s py-xs text-sm text-on-surface" onClick={() => onRevoke(grant.grantId)}>Revoke</button>}
        </div>
        <dl className="mt-m grid gap-s text-sm sm:grid-cols-2">
          <div><dt className="text-on-surface-var">Address classes</dt><dd className="text-on-surface">{grant.addressClasses.join(', ')}</dd></div>
          <div><dt className="text-on-surface-var">Proxy</dt><dd className="text-on-surface">{grant.proxyPolicy === 'required' ? 'Required' : 'Direct only'}</dd></div>
          <div><dt className="text-on-surface-var">Redirects</dt><dd className="text-on-surface">Up to {grant.redirectLimit}</dd></div>
          <div><dt className="text-on-surface-var">Response limit</dt><dd className="text-on-surface">{formatBytes(grant.byteLimit)}</dd></div>
          <div><dt className="text-on-surface-var">Expires</dt><dd className="text-on-surface">{new Date(grant.expiresAtMs).toLocaleString()}</dd></div>
          <div><dt className="text-on-surface-var">Principal</dt><dd className="break-all font-mono text-on-surface">{grant.principalId}</dd></div>
        </dl>
      </li>)}
    </ul>}
    {decisions.length > 0 && <div aria-label="Recent network decisions" className="rounded-lg bg-surface-container p-m">
      <h3 className="font-medium text-on-surface">Recent decisions</h3>
      <ul className="mt-s space-y-xs text-sm">{decisions.map((decision, index) => <li key={`${decision.checkedAtMs}-${index}`} className="flex flex-wrap justify-between gap-s">
        <span className={decision.allowed ? 'text-on-surface' : 'text-error'}>{decision.allowed ? 'Allowed' : 'Denied'} · {decision.rule}</span>
        <time className="text-on-surface-var" dateTime={new Date(decision.checkedAtMs).toISOString()}>{new Date(decision.checkedAtMs).toLocaleString()}</time>
      </li>)}</ul>
    </div>}
  </section>
}
