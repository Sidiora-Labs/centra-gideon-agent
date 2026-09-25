import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
const nativeControl = 'block h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'

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
    {error && <p role="alert" className="rounded-lg bg-danger-container p-m text-on-danger-container">{error}</p>}{notice && <p role="status" className="rounded-lg bg-primary-container p-m text-on-primary-container">{notice}</p>}
    <form className="space-y-m rounded-lg bg-surface-container px-l py-l" onSubmit={e => { e.preventDefault(); void run(async () => { setSettings((await requestJson<{ settings: Settings }>(base + '/settings', 'PUT', settings)).settings); setChats([]); setMessages([]); setNotice('Connection reference saved') }) }}>
      <label className="block">Beeper endpoint<input className={nativeControl} required disabled={busy} value={settings.base_url} onChange={e => setSettings({ ...settings, base_url: e.target.value })} /></label>
      <label className="block">Beeper credential reference<input className={nativeControl} required disabled={busy} value={settings.credential_ref} onChange={e => setSettings({ ...settings, credential_ref: e.target.value })} /></label>
      <Button type="submit" disabled={busy}>Save Beeper connection</Button>
    </form>
    <p>Connection: {settings.connected ? 'configured' : 'disconnected'}; mode: {settings.transport_mode || 'manual_refresh_only'}</p>
    {settings.connected && <Button disabled={busy} onClick={() => void run(async () => { setSettings((await requestJson<{ settings: Settings }>(base + '/disconnect', 'POST', { revision: settings.revision })).settings); setChats([]); setMessages([]); setNotice('Beeper disconnected; cached provider data cleared and queued drafts preserved as unsendable records') })}>Disconnect Beeper</Button>}
    <Button disabled={busy} onClick={() => void run(async () => { setChats((await requestJson<{ items: Message[] }>(base + '/refresh', 'POST', {})).items) })}>Refresh Beeper chats</Button>
    {chats.length === 0 && <p>No cached Beeper chats.</p>}
    {chats.map(row => <Button key={row.id} onClick={() => select(row.id)}>{row.title || row.id}</Button>)}
    <label className="block">Beeper chat ID<input className={nativeControl} disabled={busy} value={chat} onChange={e => select(e.target.value)} /></label>
    <Button disabled={busy || !chat} onClick={() => void run(async () => { setMessages((await requestJson<{ items: Message[] }>(base + '/messages?chat_id=' + encodeURIComponent(chat))).items) })}>Read cached Beeper messages</Button>
    <Button disabled={busy || !chat} onClick={() => void run(async () => { setMessages((await requestJson<{ items: Message[] }>(base + '/refresh', 'POST', { chat_id: chat })).items) })}>Refresh Beeper messages</Button>
    {messages.map(message => <article className="rounded-lg border border-outline-variant/20 bg-surface px-l py-m" key={message.id}><pre className="whitespace-pre-wrap">{message.text || '(No text)'}</pre>{message.attachments?.map(file => <div key={file.id || file.fileName}><span>{file.fileName || 'Attachment'}</span><Button disabled={busy || !file.id} onClick={() => void run(async () => { const result = await requestJson<{ bytes: number }>(base + '/assets', 'POST', { chat_id: chat, asset_id: file.id }); setNotice(`Attachment cached: ${result.bytes} bytes`) })}>Fetch attachment</Button><Button disabled={busy || !file.id} onClick={() => void run(async () => { const result = await requestJson<{ content_base64: string }>(base + '/assets?id=' + encodeURIComponent(file.id || '')); const blob = new Blob([Uint8Array.from(atob(result.content_base64), character => character.charCodeAt(0))]); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = file.fileName || 'attachment'; link.click(); URL.revokeObjectURL(link.href) })}>Download cached attachment</Button></div>)}</article>)}
    <form className="space-y-m rounded-lg bg-surface-container px-l py-l" onSubmit={e => { e.preventDefault(); void run(async () => { await requestJson(base + '/outbox', 'POST', { request_key: attempt, chat_id: chat, text: body }); setBody(''); setAttempt(crypto.randomUUID()); await reloadOutbox() }) }}>
      <label className="block">Beeper draft text<textarea className="block min-h-28 w-full resize-y rounded-md border border-outline-variant/30 bg-surface-container px-m py-s text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" required disabled={busy} value={body} onChange={e => setBody(e.target.value)} /></label><Button type="submit" disabled={busy || !chat || !body}>Queue Beeper draft</Button>
    </form>
    <h3 data-type="title-s">Durable outbox</h3>{outbox.length === 0 && <p>No queued messages.</p>}
    {outbox.map(item => <article className="rounded-lg border border-outline-variant/20 bg-surface px-l py-m" key={item.id}><p>{item.chat_id}: {item.state}; {item.delivery}</p><pre className="whitespace-pre-wrap">{item.text}</pre>{settings.connected && item.state === 'draft' && <Button disabled={busy} onClick={() => action(item, 'send')}>Send queued message</Button>}{settings.connected && item.pending_message_id && ['pending', 'unknown'].includes(item.state) && <Button disabled={busy} onClick={() => action(item, 'reconcile')}>Check send status</Button>}{item.state === 'sending' && <Button disabled={busy} onClick={() => action(item, 'recover')}>Mark interrupted send unknown</Button>}{['draft', 'pending', 'unknown', 'failed'].includes(item.state) && <Button disabled={busy} onClick={() => action(item, 'discard')}>Discard outbox item</Button>}</article>)}
  </section>
}
