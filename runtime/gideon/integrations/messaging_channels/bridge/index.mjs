import { timingSafeEqual } from 'node:crypto'
import { WebSocketServer, WebSocket } from 'ws'
import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys'
import pino from 'pino'
import qrcode from 'qrcode-terminal'

const port = Number(process.env.GIDEON_BRIDGE_PORT || 3001)
const token = process.env.GIDEON_BRIDGE_TOKEN || ''
const authDir = process.env.GIDEON_BRIDGE_AUTH_DIR || ''
if (!token || !authDir || !Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error('Bridge port, token and auth directory are required')
}

const clients = new Set()
let latestQr = ''
let status = 'disconnected'
let socket = null
let closing = false
let reconnect = null

function broadcast(payload) {
  const message = JSON.stringify(payload)
  for (const client of clients) {
    if (client.readyState === WebSocket.OPEN) client.send(message)
  }
}

function sameToken(value) {
  if (typeof value !== 'string') return false
  const supplied = Buffer.from(value)
  const expected = Buffer.from(token)
  return supplied.length === expected.length && timingSafeEqual(supplied, expected)
}

function messageText(message) {
  if (!message || typeof message !== 'object') return ''
  const nested = message.ephemeralMessage?.message || message.viewOnceMessage?.message || message
  return nested.conversation || nested.extendedTextMessage?.text || nested.imageMessage?.caption || nested.videoMessage?.caption || ''
}

function addressed(message, ownJid) {
  const context = message.extendedTextMessage?.contextInfo || message.imageMessage?.contextInfo || message.videoMessage?.contextInfo || {}
  const mentions = Array.isArray(context.mentionedJid) ? context.mentionedJid : []
  const id = String(ownJid || '').split(':')[0]
  return mentions.some(value => String(value).split(':')[0] === id)
}

async function connect() {
  if (closing) return
  const logger = pino({ level: 'silent' })
  const { state, saveCreds } = await useMultiFileAuthState(authDir)
  const { version } = await fetchLatestBaileysVersion()
  socket = makeWASocket({
    auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, logger) },
    version,
    logger,
    browser: ['Gideon', 'Channel', '1.0'],
    syncFullHistory: false,
    markOnlineOnConnect: false,
  })
  socket.ev.on('creds.update', saveCreds)
  socket.ev.on('connection.update', update => {
    if (update.qr) {
      latestQr = update.qr
      qrcode.generate(update.qr, { small: true })
      broadcast({ type: 'qr', qr: update.qr })
    }
    if (update.connection === 'open') {
      latestQr = ''
      status = 'connected'
      broadcast({ type: 'status', status })
    } else if (update.connection === 'close') {
      status = 'disconnected'
      broadcast({ type: 'status', status })
      if (!closing && reconnect === null) {
        const code = update.lastDisconnect?.error?.output?.statusCode
        reconnect = setTimeout(async () => {
          reconnect = null
          if (code === DisconnectReason.loggedOut) {
            broadcast({ type: 'error', error: 'WhatsApp session logged out; remove the saved auth directory to pair again' })
            return
          }
          try { await connect() } catch (error) { broadcast({ type: 'error', error: String(error) }) }
        }, 5000)
      }
    }
  })
  socket.ev.on('messages.upsert', ({ messages, type }) => {
    if (type !== 'notify') return
    for (const item of messages || []) {
      const key = item.key || {}
      const chat = String(key.remoteJid || '')
      if (key.fromMe || chat === 'status@broadcast' || !key.id) continue
      const sender = String(key.participant || chat)
      const text = messageText(item.message)
      if (!text) continue
      broadcast({
        type: 'message',
        id: String(key.id),
        chat,
        sender,
        content: text,
        isGroup: chat.endsWith('@g.us'),
        wasMentioned: addressed(item.message || {}, socket.user?.id),
      })
    }
  })
}

const server = new WebSocketServer({
  host: '127.0.0.1',
  port,
  verifyClient: (info, done) => done(!info.origin, info.origin ? 403 : 200),
})
server.on('connection', peer => {
  const timer = setTimeout(() => peer.close(4001, 'Authentication timeout'), 5000)
  peer.once('message', data => {
    clearTimeout(timer)
    let hello
    try { hello = JSON.parse(data.toString()) } catch { hello = {} }
    if (hello.type !== 'auth' || !sameToken(hello.token)) {
      peer.close(4003, 'Authentication failed')
      return
    }
    clients.add(peer)
    peer.send(JSON.stringify({ type: 'status', status }))
    if (latestQr) peer.send(JSON.stringify({ type: 'qr', qr: latestQr }))
    peer.on('message', async raw => {
      let command
      try { command = JSON.parse(raw.toString()) } catch { return }
      if (command.type !== 'send' || !command.id || !command.to || typeof command.text !== 'string') return
      try {
        if (status !== 'connected' || !socket) throw new Error('WhatsApp is disconnected')
        const sent = await socket.sendMessage(command.to, { text: command.text })
        peer.send(JSON.stringify({ type: 'sent', id: command.id, messageId: sent?.key?.id || '' }))
      } catch (error) {
        peer.send(JSON.stringify({ type: 'send_error', id: command.id, error: String(error) }))
      }
    })
    peer.on('close', () => clients.delete(peer))
  })
})

async function stop() {
  closing = true
  if (reconnect !== null) clearTimeout(reconnect)
  for (const peer of clients) peer.close()
  server.close()
  socket?.ws?.close()
}
process.on('SIGTERM', () => { void stop() })
process.on('SIGINT', () => { void stop() })
await connect()
