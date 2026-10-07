import { TextInput, Field, TextArea } from '../../../shared/ui/forms'
import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { BUSY_REASON } from '../../../shared/ui/unavailable'

type Settings = { base_url: string; credential_ref: string; revision: number; connected?: boolean; transport_mode?: string }
type Item = { id: string; chat_id: string; text: string; state: string; delivery: string; revision: number; pending_message_id: string | null }
type Message = { id: string; text?: string; title?: string; attachments?: { id?: string; fileName?: string }[] }
const base = '/api/capabilities/communications/beeper'
export function BeeperPanel() {
  const [settings, setSettings] = useState<Settings>({ base_url: 'http://127.0.0.1:23373', credential_ref: '', revision: 0 })
  const [outbox, setOutbox] = useState<Item[]>([])
  const [chats, setChats] = useState<Message[]>([])
  const [messages, setMessages] = useState<Message[]>([])
  const [chat, setChat] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('beeper_chat') || '')
  const [body, setBody] = useState('')
  const [attempt, setAttempt] = useState(crypto.randomUUID())
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const run = async (operation: () => Promise<void>) => { setBusy(true); setError(''); try { await operation() } catch (e) { setError(String(e)) } finally { setBusy(false) } }
  const reloadOutbox = async () => setOutbox((await requestJson<{ outbox: Item[] }>(base + '/outbox')).outbox)
  useEffect(() => { let active = true; Promise.all([requestJson<{ settings: Settings }>(base + '/settings'), requestJson<{ outbox: Item[] }>(base + '/outbox'), requestJson<{ items: Message[] }>(base + '/chats')]).then(([s, o, c]) => { if (active) { setSettings(s.settings); setOutbox(o.outbox); setChats(c.items) } }).catch(e => { if (active) setError(String(e)) }); return () => { active = false } }, [])
  const select = (id: string) => { setChat(id); setMessages([]); const params = new URLSearchParams(location.hash.split('?')[1] || ''); params.set('beeper_chat', id); location.hash = '#/capabilities/communications?' + params.toString() }
  const action = (item: Item, operation: string) => void run(async () => { try { await requestJson(`${base}/outbox/${item.id}/${operation}`, 'POST', { revision: item.revision, ...(operation === 'send' ? { confirm_send: true } : {}) }) } finally { await reloadOutbox() } })
  return <section aria-label="Beeper conversations" className="space-y-l">
    <h2 data-type="title-m">Beeper conversations</h2><p data-type="body-s" className="text-on-surface-low">Beeper Desktop must be running with its API enabled. This adapter uses manual refresh only; no background socket or poller is running. Cached pages may contain incomplete history. Pending sends are not confirmed delivery.</p>
    {error && <p role="alert" className="rounded-lg bg-error-container p-m text-on-error-container">{error}</p>}{notice && <p role="status" className="rounded-lg bg-primary-container p-m text-on-primary-container">{notice}</p>}
    <form className="space-y-m rounded-lg bg-surface-container px-l py-l" onSubmit={e => { e.preventDefault(); void run(async () => { setSettings((await requestJson<{ settings: Settings }>(base + '/settings', 'PUT', settings)).settings); setChats([]); setMessages([]); setNotice('Connection reference saved') }) }}>
      <Field label={"Beeper endpoint"}><TextInput required disabled={busy} value={settings.base_url} onChange={nextValue => setSettings({ ...settings, base_url: nextValue })} /></Field>
      <Field label={"Beeper credential reference"}><TextInput required disabled={busy} value={settings.credential_ref} onChange={nextValue => setSettings({ ...settings, credential_ref: nextValue })} /></Field>
      <Button type="submit" disabled={busy} disabledReason={busy ? BUSY_REASON : undefined}>Save Beeper connection</Button>
    </form>
    <p>Connection: {settings.connected ? 'configured' : 'disconnected'}; mode: {settings.transport_mode || 'manual_refresh_only'}</p>
    {settings.connected && <Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void run(async () => { setSettings((await requestJson<{ settings: Settings }>(base + '/disconnect', 'POST', { revision: settings.revision })).settings); setChats([]); setMessages([]); setNotice('Beeper disconnected; cached provider data cleared and queued drafts preserved as unsendable records') })}>Disconnect Beeper</Button>}
    <Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void run(async () => { setChats((await requestJson<{ items: Message[] }>(base + '/refresh', 'POST', {})).items) })}>Refresh Beeper chats</Button>
    {chats.length === 0 && <p>No cached Beeper chats.</p>}
    {chats.map(row => <Button key={row.id} onClick={() => select(row.id)}>{row.title || row.id}</Button>)}
    <Field label={"Beeper chat ID"}><TextInput disabled={busy} value={chat} onChange={nextValue => select(nextValue)} /></Field>
    <Button disabled={busy || !chat} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void run(async () => { setMessages((await requestJson<{ items: Message[] }>(base + '/messages?chat_id=' + encodeURIComponent(chat))).items) })}>Read cached Beeper messages</Button>
    <Button disabled={busy || !chat} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void run(async () => { setMessages((await requestJson<{ items: Message[] }>(base + '/refresh', 'POST', { chat_id: chat })).items) })}>Refresh Beeper messages</Button>
    {messages.map(message => <article className="rounded-lg border border-outline-variant/20 bg-surface px-l py-m" key={message.id}><pre className="whitespace-pre-wrap">{message.text || '(No text)'}</pre>{message.attachments?.map(file => <div key={file.id || file.fileName}><span>{file.fileName || 'Attachment'}</span><Button ariaLabel={`Fetch attachment: ${file.fileName || 'unnamed attachment'}`} disabled={busy || !file.id} disabledReason={busy ? BUSY_REASON : !file.id ? 'This attachment has no downloadable provider reference.' : undefined} onClick={() => void run(async () => { const result = await requestJson<{ bytes: number }>(base + '/assets', 'POST', { chat_id: chat, asset_id: file.id }); setNotice(`Attachment cached: ${result.bytes} bytes`) })}>Fetch attachment</Button><Button ariaLabel={`Download cached attachment: ${file.fileName || 'unnamed attachment'}`} disabled={busy || !file.id} disabledReason={busy ? BUSY_REASON : !file.id ? 'This attachment has no downloadable provider reference.' : undefined} onClick={() => void run(async () => { const result = await requestJson<{ content_base64: string }>(base + '/assets?id=' + encodeURIComponent(file.id || '')); const blob = new Blob([Uint8Array.from(atob(result.content_base64), character => character.charCodeAt(0))]); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = file.fileName || 'attachment'; link.click(); URL.revokeObjectURL(link.href) })}>Download cached attachment</Button></div>)}</article>)}
    <form className="space-y-m rounded-lg bg-surface-container px-l py-l" onSubmit={e => { e.preventDefault(); void run(async () => { await requestJson(base + '/outbox', 'POST', { request_key: attempt, chat_id: chat, text: body }); setBody(''); setAttempt(crypto.randomUUID()); await reloadOutbox() }) }}>
      <Field label={"Beeper draft text"}><TextArea required disabled={busy} value={body} onChange={nextValue => setBody(nextValue)} /></Field><Button type="submit" disabled={busy || !chat || !body} disabledReason={busy ? BUSY_REASON : undefined}>Queue Beeper draft</Button>
    </form>
    <h3 data-type="title-s">Durable outbox</h3>{outbox.length === 0 && <p>No queued messages.</p>}
    {outbox.map(item => <article className="rounded-lg border border-outline-variant/20 bg-surface px-l py-m" key={item.id}><p>{item.chat_id}: {item.state}; {item.delivery}</p><pre className="whitespace-pre-wrap">{item.text}</pre>{settings.connected && item.state === 'draft' && <Button ariaLabel={`Send queued message: ${item.text.trim().slice(0, 80) || 'message without text'}`} disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => action(item, 'send')}>Send queued message</Button>}{settings.connected && item.pending_message_id && ['pending', 'unknown'].includes(item.state) && <Button ariaLabel={`Check send status: ${item.text.trim().slice(0, 80) || 'message without text'}`} disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => action(item, 'reconcile')}>Check send status</Button>}{item.state === 'sending' && <Button ariaLabel={`Mark interrupted send unknown: ${item.text.trim().slice(0, 80) || 'message without text'}`} disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => action(item, 'recover')}>Mark interrupted send unknown</Button>}{['draft', 'pending', 'unknown', 'failed'].includes(item.state) && <Button ariaLabel={`Discard outbox item: ${item.text.trim().slice(0, 80) || 'message without text'}`} disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => action(item, 'discard')}>Discard outbox item</Button>}</article>)}
  </section>
}
