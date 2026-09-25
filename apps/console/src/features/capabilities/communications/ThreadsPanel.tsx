import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Thread = { source: string; source_account_id: string; person_id: string; thread_id: string; state: string; reason: string; as_of: string | null; latest: { summary: string }; message_count: number }
type Report = { people: { person: { id: string; name: string }; care: { state: string } }[]; threads: Thread[]; timezone: string }

export function ThreadsPanel() {
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState('')
  const [version, setVersion] = useState(0)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    requestJson<Report>('/api/capabilities/communications/threads')
      .then(value => { if (active) setReport(value) })
      .catch(e => { if (active) setError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [version])
  return <section aria-label="Thread care evidence" className="my-5 space-y-3 rounded border border-outline p-3">
    <h2>Thread care evidence</h2><p>These results describe imported observations, not live account status. Incomplete or stale coverage stays unknown.</p>
    <Button onClick={() => setVersion(v => v + 1)} disabled={loading}>Refresh thread evidence</Button>
    {error && <p role="alert" className="text-danger">{error}</p>}
    {loading ? <p role="status">Loading thread evidence…</p> : report && <>
      <p>Timezone: {report.timezone}</p>
      {!report.threads.length && <p>No thread observations have been imported.</p>}
      <ul>{report.threads.map(t => <li className="my-3 border-b border-outline pb-3" key={`${t.source}/${t.source_account_id}/${t.person_id}/${t.thread_id}`}>
        <a className="text-primary underline" href={`#/capabilities/communications?person=${t.person_id}`}>{report.people.find(p => p.person.id === t.person_id)?.person.name || 'Unknown person'}</a>
        <p>Thread {t.thread_id} · {t.state} · {t.message_count} observed messages</p>
        <p>{t.latest.summary}</p><p>{t.reason}</p>
        <p>Source: {t.source} · {t.source_account_id} · As of: {t.as_of || 'Unknown'}</p>
      </li>)}</ul>
    </>}
  </section>
}
