import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Session = { id: string; title: string; status?: string; connection_id: string; provenance: 'remote' }
type Connection = { id: string; label: string; base_url: string; credential_ref: string; retained_sessions: Session[] }
type Message = { id: string; role: string; content: string; source: { connection_id: string; session_id: string; kind: 'remote' } }

export function RemoteSessions({ baseUrl = '' }: { baseUrl?: string }) {
  const endpoint = `${baseUrl}/api/capabilities/platform/remote-sessions`
  const [connections, setConnections] = useState<Connection[]>([])
  const [active, setActive] = useState<{ connection: string; session: string }>()
  const [messages, setMessages] = useState<Message[]>([])
  const [composer, setComposer] = useState('')
  const [reply, setReply] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    try { setConnections((await readJson<{ connections: Connection[] }>(await gatewayRequest(endpoint))).connections); setError('') }
    catch { setError('Remote agent sessions unavailable.') }
  }
  useEffect(() => { void load() }, [baseUrl])

  const refresh = async (connection: string) => {
    setBusy(true); setError('')
    try {
      const body = await readJson<{ sessions: Session[] }>(await gatewayRequest(`${endpoint}/${encodeURIComponent(connection)}/sessions`))
      setConnections(rows => rows.map(row => row.id === connection ? { ...row, retained_sessions: body.sessions } : row))
    } catch { setError('Remote runtime unavailable.') } finally { setBusy(false) }
  }
  const open = async (connection: string, session: string) => {
    setActive({ connection, session }); setReply(''); setError('')
    try { setMessages((await readJson<{ messages: Message[] }>(await gatewayRequest(`${endpoint}/${encodeURIComponent(connection)}/sessions/${encodeURIComponent(session)}/history`))).messages) }
    catch { setMessages([]); setError('Remote session history unavailable.') }
  }
  const send = async () => {
    if (!active || !composer.trim() || busy) return
    setBusy(true); setError(''); setReply('')
    try {
      const response = await gatewayRequest(`${endpoint}/${encodeURIComponent(active.connection)}/sessions/${encodeURIComponent(active.session)}/messages/stream`, 'POST', { message: composer.trim(), attachments: [] })
      if (!response.body) throw new Error('Streaming unavailable')
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let text = ''
      while (true) { const { done, value } = await reader.read(); if (done) break; text += decoder.decode(value, { stream: true }); setReply(text) }
      setComposer('')
    } catch { setError('Remote reply stream failed.') } finally { setBusy(false) }
  }

  return <section aria-label="Remote agent sessions" className="space-y-s">
    <h2 className="text-l">Remote agent sessions</h2>
    <p>Sessions remain owned by their configured external runtime. Gideon retains identity and provenance.</p>
    {error && <p role="alert">{error}</p>}
    {connections.length === 0 && !error && <p>No remote agent connections configured.</p>}
    {connections.map(connection => <article key={connection.id}>
      <h3>{connection.label}</h3><p>{connection.base_url} · credential {connection.credential_ref}</p>
      <Button loading={busy} onClick={() => void refresh(connection.id)}>Refresh remote sessions</Button>
      <ul>{connection.retained_sessions.map(session => <li key={session.id}><button type="button" onClick={() => void open(connection.id, session.id)}>{session.title}</button> <span>{session.provenance}</span></li>)}</ul>
    </article>)}
    {active && <div aria-label="Remote conversation">
      <h3>Session {active.session}</h3>
      <ol>{messages.map(message => <li key={message.id}><strong>{message.role}</strong>: {message.content} <small>{message.source.kind}</small></li>)}</ol>
      {reply && <pre aria-label="Remote reply stream">{reply}</pre>}
      <label>Message<textarea value={composer} onChange={event => setComposer(event.target.value)} /></label>
      <Button loading={busy} onClick={() => void send()}>Send to remote agent</Button>
    </div>}
  </section>
}
