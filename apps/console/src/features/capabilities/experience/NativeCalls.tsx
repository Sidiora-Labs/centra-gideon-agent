import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, Select, TextArea, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'
type Attempt = { id: string; command: string; state: string; result: string }
type Snapshot = { readiness: { available: boolean; errors: string[]; target_name: string | null; detail: string }; requests: Attempt[] }
export default function NativeCalls({ baseUrl = '/api/capabilities/experience' }: { baseUrl?: string }) {
  const [data, setData] = useState<Snapshot | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState('')
  const [conversation, setConversation] = useState('')
  const [transcript, setTranscript] = useState('')
  const [notice, setNotice] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const refresh = async () => setData(await requestJson<Snapshot>(baseUrl + '/native-calls'))
  useEffect(() => { void refresh().catch(e => setError(String(e))) }, [baseUrl])
  async function command(command: string) {
    setBusy(true); setError('')
    try { await requestJson(baseUrl + '/native-calls', 'POST', { command, request_id: crypto.randomUUID() }); await refresh() }
    catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  async function saveTranscript() {
    setBusy(true); setError(''); setNotice('')
    try {
      await requestJson(baseUrl + `/native-calls/${selected}/handoff`, 'POST', { conversation, messages: [{ role: 'user', text: transcript }], request_id: requestId })
      setNotice('User-supplied transcript saved. Agent continuation has not started.'); setRequestId(crypto.randomUUID())
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  return <Surface className="p-m"><section aria-label="Native audio calls" className="space-y-m">
    <h2 data-type="title-m">Native audio calls</h2>
    <p>Machine-local FaceTime control requires a configured Mac. Remote desktop execution and automatic audio transcription are not available here.</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!data ? <p>Loading native call readiness…</p> : <>
      <p role="status">{data.readiness.available ? 'Native control can be requested; call audio remains unverified.' : 'Native control unavailable'}</p>
      {data.readiness.errors.map((message, i) => <p key={i}>{message}</p>)}<p>{data.readiness.detail}</p>
      {data.readiness.target_name && <p>Configured recipient: {data.readiness.target_name}</p>}
      <div className="flex flex-wrap gap-2">{['probe', 'call', 'answer', 'hangup'].map(operation => <Button key={operation} disabled={busy || !data.readiness.available} onClick={() => void command(operation)}>{operation}</Button>)}<Button disabled={busy} onClick={() => void refresh().catch(e => setError(String(e)))}>Refresh native readiness</Button></div>
      <ul>{data.requests.map(row => <li key={row.id}>{row.command}: {row.state} — {row.result}</li>)}</ul>
      <h3 data-type="title-m">Save a supplied transcript</h3><p>This records text you provide; it does not verify that a call connected or that audio was captured.</p>
      <Field label="Native request"><Select value={selected} onChange={value => { setSelected(value); setRequestId(crypto.randomUUID()) }} options={[{value:'',label:'Choose a request'},...data.requests.map(row=>({value:row.id,label:`${row.command} · ${row.state}`}))]} /></Field>
      <Field label="Existing conversation"><TextInput value={conversation} onChange={value => { setConversation(value); setRequestId(crypto.randomUUID()) }} /></Field>
      <Field label="Supplied transcript"><TextArea rows={8} value={transcript} onChange={value => { setTranscript(value); setRequestId(crypto.randomUUID()) }} /></Field>
      <Button disabled={busy || !selected || !conversation || !transcript.trim()} onClick={() => void saveTranscript()}>Save transcript to conversation</Button>
    </>}
  </section></Surface>
}
