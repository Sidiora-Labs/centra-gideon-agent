import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Data = {
  domains: { domain: string; state: string; setup_route: string }[]
  detectors: string[]
  other_detectors: string
}

export function DomainReadiness({ baseUrl = '' }: { baseUrl?: string }) {
  const [data, setData] = useState<Data>()
  const [error, setError] = useState('')
  const load = async (runScan = false) => {
    setError('')
    try {
      const response = await gatewayRequest(
        `${baseUrl}/api/capabilities/platform/domain-readiness`,
        runScan ? 'POST' : 'GET',
      )
      setData(await readJson<Data>(response))
    } catch {
      setError('Domain sources unavailable.')
    }
  }
  useEffect(() => { void load() }, [baseUrl])
  return <section aria-label="Domain readiness" className="space-y-s">
    <h2 className="text-l">Domain readiness</h2>
    <div className="flex flex-wrap gap-s">
      <Button onClick={() => void load(true)}>Check domain alerts</Button>
      <a href="#/notifications">Open existing notifications</a>
    </div>
    {error && <p role="alert">{error}</p>}
    {data && <>
      <ul>{data.domains.map(row => <li key={row.domain}>{row.domain}: {row.state} <a href={row.setup_route}>Open domain</a></li>)}</ul>
      <p>Active detectors: {data.detectors.join(', ')}</p>
      <p>Additional domain detectors: {data.other_detectors}</p>
    </>}
  </section>
}
