import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, describe, expect, it } from 'vitest'
import { canonicalMessages, receivedPrompt } from './controller'
import type { ChatDetail } from './types'

const root = resolve(process.cwd(), '../..')
const children: ChildProcessWithoutNullStreams[] = []
const directories: string[] = []
let vite: ViteDevServer | undefined
let debuggerSocket: WebSocket | undefined

afterAll(async () => {
  debuggerSocket?.close()
  for (const child of children) child.kill('SIGTERM')
  if (vite) await vite.close()
  for (const directory of directories) await rm(directory, { recursive: true, force: true })
})

async function port(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const address = server.address()
  const value = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return value
}

async function server(origin: string): Promise<string> {
  const child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3',
    [join(root, 'apps/assistant/test-support/conversation_server.py'), origin],
    { env: { ...process.env, PYTHONPATH: join(root, 'runtime') } })
  children.push(child)
  const line = await new Promise<string>((done, reject) => {
    let output = ''
    let errors = ''
    const timeout = setTimeout(() => reject(new Error(`Conversation server timed out: ${errors}`)), 15000)
    child.stdout.on('data', chunk => {
      output += String(chunk)
      if (output.includes('\n')) { clearTimeout(timeout); done(output.split('\n')[0]) }
    })
    child.stderr.on('data', chunk => { errors += String(chunk) })
    child.once('exit', code => { clearTimeout(timeout); reject(new Error(`Conversation server exited ${code}: ${errors}`)) })
  })
  return `http://127.0.0.1:${(JSON.parse(line) as { api_port: number }).api_port}`
}

async function browser(address: string): Promise<{
  evaluate: (expression: string) => Promise<any>
  command: (method: string, params?: Record<string, unknown>) => Promise<any>
}> {
  const directory = await mkdtemp(join(tmpdir(), 'gideon-conversation-browser-'))
  directories.push(directory)
  const debuggingPort = await port()
  const child = spawn(process.env.CHROMIUM_BIN || 'chromium', [
    '--headless', '--no-sandbox', '--disable-gpu', '--disable-background-networking',
    `--remote-debugging-port=${debuggingPort}`, `--user-data-dir=${directory}`, 'about:blank',
  ], { env: Object.fromEntries(Object.entries(process.env).filter(([key]) => key !== 'OPENAI_API_KEY')) })
  children.push(child)
  let target: { webSocketDebuggerUrl: string } | undefined
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${debuggingPort}/json`)).json() as
        Array<{ type: string; webSocketDebuggerUrl: string }>
      target = targets.find(item => item.type === 'page')
      if (target) break
    } catch { await new Promise(done => setTimeout(done, 100)) }
    if (!target) await new Promise(done => setTimeout(done, 100))
  }
  if (!target) throw new Error('Chromium did not start')
  debuggerSocket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise<void>((done, reject) => {
    debuggerSocket!.addEventListener('open', () => done(), { once: true })
    debuggerSocket!.addEventListener('error', () => reject(new Error('Chromium debugger failed')), { once: true })
  })
  let nextId = 0
  const pending = new Map<number, { done: (value: any) => void; reject: (error: Error) => void }>()
  debuggerSocket.addEventListener('message', event => {
    const response = JSON.parse(String(event.data)) as { id?: number; result?: any; error?: { message: string } }
    if (!response.id) return
    const request = pending.get(response.id)
    if (!request) return
    pending.delete(response.id)
    if (response.error) request.reject(new Error(response.error.message))
    else request.done(response.result)
  })
  const command = (method: string, params: Record<string, unknown> = {}) => new Promise<any>((done, reject) => {
    const id = ++nextId
    pending.set(id, { done, reject })
    debuggerSocket!.send(JSON.stringify({ id, method, params }))
  })
  await command('Page.navigate', { url: address })
  const evaluate = async (expression: string) => {
    const response = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text)
    return response.result?.value
  }
  return { evaluate, command }
}

describe('canonical Gideon conversation', () => {
  it('retains producer IDs and recognizes only the submitted user timestamp', () => {
    const detail: ChatDetail = { key: 'session-a', title: '', running: false, messages: [
      { role: 'user', content: 'hello', ts: '2026-09-27T12:00:00Z' },
      { role: 'assistant', content: 'answer', ts: '2026-09-27T12:00:01Z', meta: { id: 'turn-1' } },
    ] }
    expect(canonicalMessages(detail).map(message => message.id)).toEqual([
      'session-a:message:user:2026-09-27T12:00:00Z:0', 'session-a:message:turn-1',
    ])
    expect(receivedPrompt(detail, '2026-09-27T12:00:00Z')).toBe(true)
    expect(receivedPrompt(detail, '2026-09-27T12:00:01Z')).toBe(false)
  })

  it('uses authenticated browser requests, canonical history and the real gateway socket', async () => {
    const webPort = await port()
    const origin = `http://127.0.0.1:${webPort}`
    const api = await server(origin)
    const entry = `
import { ConversationController } from '/src/shared/conversation/controller.ts'
import { ownerScope, signInOwner, signOutOwner } from '/src/shared/auth.web.tsx'
window.controller = new ConversationController()
window.signInOwner = signInOwner
window.signOutOwner = signOutOwner
window.ownerScope = ownerScope
window.loaded = true
`
    vite = await createServer({ configFile: false, root: join(root, 'apps/assistant'),
      plugins: [{ name: 'conversation-entry',
        resolveId(id) { if (id === '/conversation-entry.ts') return '\0conversation-entry' },
        load(id) { if (id === '\0conversation-entry') return entry },
        configureServer(server) { server.middlewares.use('/conversation', (_request, response) => {
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end('<!doctype html><script type="module" src="/conversation-entry.ts"></script>')
        }) },
      }],
      server: { host: '127.0.0.1', port: webPort, strictPort: true, proxy: { '/api': { target: api, ws: true } } },
    })
    await vite.listen()
    const { evaluate, command } = await browser(`${origin}/conversation`)
    await evaluate('new Promise((done, reject) => { let n=0; const timer=setInterval(() => { if (window.loaded) { clearInterval(timer); done(true) } else if (++n > 100) { clearInterval(timer); reject(Error("entry unavailable")) } }, 50) })')
    const result = await evaluate(`(async () => {
      const expectAnswer = ${Boolean(process.env.GIDEON_TEST_MODEL)}
      const owner = await window.signInOwner('conversation-owner', 'correct-horse-battery-staple')
      window.controller.setOwner(window.ownerScope(location.origin, owner))
      const session = await window.controller.create()
      await new Promise((done, reject) => { let n=0; const timer=setInterval(() => { if (window.controller.snapshot().connected) { clearInterval(timer); done() } else if (++n > 100) { clearInterval(timer); reject(Error('socket did not connect')) } }, 50) })
      const socket = window.controller.socket
      const frames = []
      socket.addEventListener('message', event => {
        const frame = JSON.parse(event.data)
        if (frame.data?.session === session) frames.push({ type: frame.type, role: frame.data.role || '' })
      })
      window.controller.setDraft('  Reply with one word: ready.  ')
      await window.controller.send()
      if (expectAnswer) {
        await new Promise((done, reject) => { let n=0; const timer=setInterval(() => { if (frames.some(frame => frame.type === 'chat_done')) { clearInterval(timer); done() } else if (++n > 300) { clearInterval(timer); reject(Error('selected session completion not received: ' + JSON.stringify(frames))) } }, 50) })
      }
      const detail = await (await fetch('/api/chat/sessions/' + encodeURIComponent(session), { credentials: 'same-origin', headers: { 'X-Gideon-API-Version': '1', 'X-Session-Key': 'dashboard:ui' } })).json()
      await window.controller.refresh()
      const snapshot = window.controller.snapshot()
      const evidence = { session, connected: snapshot.connected, persisted: detail.messages.filter(m => m.role === 'user' && m.content === 'Reply with one word: ready.').length,
        visible: snapshot.messages.filter(m => m.role === 'user' && m.content === 'Reply with one word: ready.').length,
        assistantPersisted: detail.messages.filter(m => m.role === 'assistant' && m.content.trim()).map(m => m.content),
        assistantVisible: snapshot.messages.filter(m => m.role === 'assistant' && m.content.trim()).map(m => m.content),
        draft: snapshot.draft, ids: snapshot.messages.map(m => m.id) }
      socket.close()
      await new Promise((done, reject) => { let n=0; const timer=setInterval(() => { if (!window.controller.snapshot().connected) { clearInterval(timer); done() } else if (++n > 100) { clearInterval(timer); reject(Error('socket did not disconnect')) } }, 50) })
      await new Promise((done, reject) => { let n=0; const timer=setInterval(() => { if (window.controller.snapshot().connected) { clearInterval(timer); done() } else if (++n > 100) { clearInterval(timer); reject(Error('socket did not reconnect')) } }, 50) })
      const reconnected = window.controller.snapshot().messages.filter(m => m.role === 'user' && m.content === 'Reply with one word: ready.').length
      window.controller.setDraft('Reply with one word: ready again.')
      const secondSend = window.controller.send()
      window.controller.setDraft('Edited while sending')
      await secondSend
      const retainedEdit = window.controller.snapshot().draft
      await window.controller.open('missing-session')
      window.controller.setDraft('Retain this draft')
      await window.controller.send()
      const failed = { draft: window.controller.snapshot().draft, phase: window.controller.snapshot().phase }
      await window.signOutOwner()
      window.controller.setOwner(null)
      return { ...evidence, frames, socketChanged: window.controller.socket !== socket, reconnected, retainedEdit, failed, cleared: window.controller.snapshot() }
    })()`)
    expect(result.session).toMatch(/^chat-/)
    expect(result.connected).toBe(true)
    expect(result.persisted).toBe(1)
    expect(result.visible).toBe(1)
    expect(result.draft).toBe('')
    expect(new Set(result.ids).size).toBe(result.ids.length)
    if (process.env.GIDEON_TEST_MODEL) {
      expect(result.frames.map((frame: { type: string }) => frame.type)).toContain('chat_chunk')
      expect(result.frames.map((frame: { type: string }) => frame.type)).toContain('chat_done')
      expect(result.frames).toContainEqual({ type: 'chat_message', role: 'assistant' })
      expect(result.assistantPersisted.length).toBeGreaterThan(0)
      expect(result.assistantVisible).toEqual(result.assistantPersisted)
    }
    console.log('Selected session websocket events:', result.frames.map((frame: { type: string }) => frame.type))
    console.log('Persisted assistant answer:', result.assistantPersisted)
    expect(result.socketChanged).toBe(true)
    expect(result.reconnected).toBe(1)
    expect(result.retainedEdit).toBe('Edited while sending')
    expect(result.failed).toEqual({ draft: 'Retain this draft', phase: 'failed' })
    expect(result.cleared).toMatchObject({ sessionId: null, draft: '', messages: [], phase: 'signed-out' })

    await evaluate(`(async () => {
      const owner = await window.signInOwner('conversation-owner', 'correct-horse-battery-staple')
      window.recoveryOwner = owner
      window.controller.setOwner(window.ownerScope(location.origin, owner))
      window.secondSession = await window.controller.create()
      await window.controller.open(${JSON.stringify(result.session)})
      return true
    })()`)
    await command('Fetch.enable', { patterns: [
      { urlPattern: `${origin}/api/chat?ws=1`, requestStage: 'Request' },
      { urlPattern: `${origin}/api/chat/sessions/${result.session}`, requestStage: 'Request' },
    ] })
    let postFailed = false
    let faultDone = false
    let newPostRequest = ''
    let pauseNewPost: (requestId: string) => void = () => {}
    const newPostPaused = new Promise<string>(done => { pauseNewPost = done })
    const fault = new Promise<void>((done, reject) => {
      const timer = setTimeout(() => reject(new Error('Gateway fault sequence timed out')), 10000)
      debuggerSocket!.addEventListener('message', event => {
        const frame = JSON.parse(String(event.data)) as { method?: string; params?: { requestId: string; request: { method: string; url: string } } }
        if (frame.method !== 'Fetch.requestPaused' || !frame.params) return
        const { requestId, request } = frame.params
        if (request.method === 'POST' && request.url.includes('/api/chat?ws=1')) {
          if (!postFailed) {
            postFailed = true
            void command('Fetch.failRequest', { requestId, errorReason: 'ConnectionClosed' }).catch(reject)
          } else {
            newPostRequest = requestId
            pauseNewPost(requestId)
          }
        } else if (postFailed && request.method === 'GET' && request.url.endsWith(`/api/chat/sessions/${result.session}`)) {
          void (async () => {
            await evaluate(`(async () => {
              window.controller.setOwner(null)
              window.controller.setOwner(window.ownerScope(location.origin, window.recoveryOwner))
              await window.controller.open(window.secondSession)
              window.controller.setDraft('New owner send')
              window.newSend = window.controller.send()
              return true
            })()`)
            await newPostPaused
            await command('Fetch.failRequest', { requestId, errorReason: 'ConnectionClosed' })
            faultDone = true
            clearTimeout(timer)
            done()
          })().catch(reject)
        } else {
          void command('Fetch.continueRequest', { requestId }).catch(reject)
        }
      })
    })
    const stale = evaluate(`(async () => {
      window.controller.setDraft('Network fault while sending')
      await window.controller.send()
      return { state: window.controller.snapshot(), submitting: window.controller.submitting }
    })()`)
    const [staleResult] = await Promise.all([stale, fault])
    expect(postFailed && faultDone).toBe(true)
    expect(staleResult.state).toMatchObject({ draft: 'New owner send', phase: 'sending', error: '' })
    expect(staleResult.state.sessionId).not.toBe(result.session)
    expect(staleResult.submitting).toBe(true)
    expect(newPostRequest).not.toBe('')
    await command('Fetch.failRequest', { requestId: newPostRequest, errorReason: 'ConnectionClosed' })
    const newerResult = await evaluate('(async () => { await window.newSend; return { state: window.controller.snapshot(), submitting: window.controller.submitting } })()')
    await command('Fetch.disable')
    expect(newerResult.state).toMatchObject({ draft: 'New owner send', phase: 'failed' })
    expect(newerResult.submitting).toBe(false)
  }, 45000)
})
