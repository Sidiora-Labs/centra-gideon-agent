import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'

type Config = { enabled: boolean; automatic_replies: boolean; bot_credential_ref: string; webhook_credential_ref: string; allowed_chat_ids: number[]; allowed_user_ids: number[]; revision: number }
type Delivery = { id: string; chat_id: number; text: string; state: string; message_id: number | null }
const base = '/api/capabilities/communications/telegram'
export function TelegramPanel() {
  const [config, setConfig] = useState<Config>({ enabled: false, automatic_replies: false, bot_credential_ref: '', webhook_credential_ref: '', allowed_chat_ids: [], allowed_user_ids: [], revision: 0 })
  const [chats, setChats] = useState('')
  const [users, setUsers] = useState('')
  const [deliveries, setDeliveries] = useState<Delivery[]>([])
  const [command, setCommand] = useState('/care')
  const [result, setResult] = useState('')
  const [chat, setChat] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('telegram_chat') || '')
  const [text, setText] = useState('')
  const [attempt, setAttempt] = useState(crypto.randomUUID())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const reload = async () => setDeliveries((await requestJson<{ deliveries: Delivery[] }>(base + '/deliveries')).deliveries)
  const run = async (operation: () => Promise<void>) => { setBusy(true); setError(''); try { await operation() } catch (e) { setError(String(e)) } finally { setBusy(false) } }
  useEffect(() => { let active = true; Promise.all([requestJson<{ config: Config }>(base + '/config'), requestJson<{ deliveries: Delivery[] }>(base + '/deliveries')]).then(([c, d]) => { if (active) { setConfig(c.config); setChats(c.config.allowed_chat_ids.join(',')); setUsers(c.config.allowed_user_ids.join(',')); setDeliveries(d.deliveries) } }).catch(e => { if (active) setError(String(e)) }); return () => { active = false } }, [])
  return <section aria-label="Telegram operations" className="space-y-3 rounded border p-4"><h2>Telegram operations</h2><p>Operational commands read this runtime. Delivery requires an enabled connection and an allowed chat. API acceptance does not prove recipient reading.</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <form onSubmit={e => { e.preventDefault(); void run(async () => { const data = { ...config, allowed_chat_ids: chats.split(',').filter(Boolean).map(Number), allowed_user_ids: users.split(',').filter(Boolean).map(Number) }; setConfig((await requestJson<{ config: Config }>(base + '/config', 'PUT', data)).config); setNotice('Telegram settings saved') }) }}>
      <label><input type="checkbox" checked={config.enabled} onChange={e => setConfig({ ...config, enabled: e.target.checked })} />Enable Telegram delivery</label>
      <label><input type="checkbox" checked={config.automatic_replies} onChange={e => setConfig({ ...config, automatic_replies: e.target.checked })} />Automatically reply to authorized operational commands</label>
      <label>Bot credential reference<input required value={config.bot_credential_ref} onChange={e => setConfig({ ...config, bot_credential_ref: e.target.value })} /></label>
      <label>Webhook secret reference<input required value={config.webhook_credential_ref} onChange={e => setConfig({ ...config, webhook_credential_ref: e.target.value })} /></label>
      <label>Allowed Telegram chat IDs<input required value={chats} onChange={e => setChats(e.target.value)} /></label><label>Allowed Telegram user IDs<input required value={users} onChange={e => setUsers(e.target.value)} /></label><button disabled={busy}>Save Telegram settings</button>
    </form>
    <label>Operational command<select value={command} onChange={e => { setCommand(e.target.value); setResult('') }}><option>/care</option><option>/people</option><option>/status</option></select></label><button disabled={busy} onClick={() => void run(async () => { setResult((await requestJson<{ text: string }>(base + '/command', 'POST', { command })).text) })}>Preview Telegram command</button>{result && <pre className="whitespace-pre-wrap">{result}</pre>}
    <form onSubmit={e => { e.preventDefault(); void run(async () => { await requestJson(base + '/deliveries', 'POST', { request_key: attempt, chat_id: Number(chat), text }); setText(''); setAttempt(crypto.randomUUID()); await reload() }) }}>
      <label>Notification Telegram chat<input required value={chat} onChange={e => { setChat(e.target.value); const params = new URLSearchParams(location.hash.split('?')[1] || ''); params.set('telegram_chat', e.target.value); location.hash = '#/capabilities/communications?' + params.toString() }} /></label><label>Telegram notification text<textarea required value={text} onChange={e => setText(e.target.value)} /></label><button disabled={busy || !chat || !text}>Queue Telegram notification</button>
    </form>
    <button disabled={busy} onClick={() => void run(reload)}>Refresh Telegram attempts</button>{deliveries.length === 0 && <p>No Telegram delivery attempts.</p>}{deliveries.map(row => <article key={row.id}><p>Chat {row.chat_id}: {row.state}</p><pre className="whitespace-pre-wrap">{row.text}</pre>{row.state === 'queued' && <button disabled={busy} onClick={() => void run(async () => { try { await requestJson(`${base}/deliveries/${row.id}/send`, 'POST', { confirm_send: true }) } finally { await reload() } })}>Send queued Telegram notification</button>}{row.state === 'sending' && <p>Outcome unconfirmed. Do not resend.</p>}{row.message_id !== null && <p>Telegram message ID: {row.message_id}</p>}</article>)}</section>
}
