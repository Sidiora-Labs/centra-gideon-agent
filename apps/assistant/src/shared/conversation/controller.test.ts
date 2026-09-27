import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, describe, expect, it } from 'vitest'
import { canonicalMessages, receivedPrompt, reconcileStreamChunk, reconcileToolEvent, streamCursorDisposition } from './controller'
import type { ChatDetail, ChatSocketEvent, ConversationStreamCursor } from './types'

const root = resolve(process.cwd(), '../..')
const children: ChildProcessWithoutNullStreams[] = []
const directories: string[] = []
let vite: ViteDevServer | undefined
let debuggerSocket: WebSocket | undefined

async function stopChild(child: ChildProcessWithoutNullStreams): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null) return
  const exited = new Promise<void>(done => child.once('exit', () => done()))
  child.kill('SIGTERM')
  await exited
}

afterAll(async () => {
  debuggerSocket?.close()
  await Promise.all(children.map(stopChild))
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
  it('reconciles chunk replay by epoch, turn and sequence rather than text overlap', () => {
    const terminal: ConversationStreamCursor = { stream_epoch: 'epoch-a', stream_turn: 4, stream_seq: 3 }
    const repeatedFrames = [1, 2].map(stream_seq => ({ content: 'ha', cursor: {
      stream_epoch: 'epoch-a', stream_turn: 4, stream_seq,
    } }))
    expect(repeatedFrames.map(frame => streamCursorDisposition(terminal, frame.cursor))).toEqual(['stale', 'stale'])
    expect(streamCursorDisposition(terminal,
      { stream_epoch: 'epoch-a', stream_turn: 4, stream_seq: 4 })).toBe('same-turn')
    expect(streamCursorDisposition(terminal,
      { stream_epoch: 'epoch-a', stream_turn: 5, stream_seq: 1 })).toBe('new-turn')
    expect(streamCursorDisposition(terminal,
      { stream_epoch: 'epoch-b', stream_turn: 1, stream_seq: 1 })).toBe('new-turn')
    expect(repeatedFrames.map(frame => frame.content)).toEqual(['ha', 'ha'])

    const active: ConversationStreamCursor = { ...terminal, stream_seq: 2 }
    const snapshot = [{ id: 'session-a:message:stream', role: 'assistant', content: 'haha', streaming: true,
      meta: { stream_epoch: active.stream_epoch, stream_turn: active.stream_turn, stream_seq: active.stream_seq } }]
    expect(reconcileStreamChunk('session-a', snapshot, active, 'ha', repeatedFrames[0].cursor)).toBeNull()
    const overlapped = reconcileStreamChunk('session-a', snapshot, active, 'ha',
      { stream_epoch: active.stream_epoch, stream_turn: active.stream_turn, stream_seq: 3 })!
    expect(overlapped.messages[0].content).toBe('hahaha')

    const terminalSnapshot = [{ id: 'session-a:message:answer', role: 'assistant', content: 'haha' }]
    expect(reconcileStreamChunk('session-a', terminalSnapshot, terminal, 'ha',
      { stream_epoch: terminal.stream_epoch, stream_turn: terminal.stream_turn, stream_seq: 3 })).toBeNull()
    const nextTurn = reconcileStreamChunk('session-a', terminalSnapshot, terminal, 'ha',
      { stream_epoch: terminal.stream_epoch, stream_turn: terminal.stream_turn + 1, stream_seq: 1 })!
    expect(nextTurn.messages).toEqual([
      terminalSnapshot[0],
      expect.objectContaining({ id: 'session-a:live:epoch-a:5:1', content: 'ha', streaming: true,
        meta: { stream_epoch: 'epoch-a', stream_turn: 5, stream_seq: 1 } }),
    ])
    const restarted = reconcileStreamChunk('session-a', nextTurn.messages, nextTurn.cursor, 'ha',
      { stream_epoch: 'epoch-b', stream_turn: 1, stream_seq: 1 })!
    expect(restarted.messages.filter(message => message.streaming)).toHaveLength(1)
    expect(restarted.messages.at(-1)?.content).toBe('ha')
  })

  it('keeps assistant segments on either side of native tool frames in live and hydrated history', () => {
    const beforeTool: ConversationStreamCursor = { stream_epoch: 'epoch-a', stream_turn: 8, stream_seq: 1 }
    const afterTool: ConversationStreamCursor = { ...beforeTool, stream_seq: 2 }
    const frames = [
      { type: 'tool_call', data: { tool_call_id: 'call-1', tool: 'write_file', update: false } },
      { type: 'tool_result', data: { tool_call_id: 'call-1', tool: 'write_file', output: 'saved' } },
    ] satisfies readonly ChatSocketEvent[]

    const first = reconcileStreamChunk('session-a', [], null, 'before ', beforeTool)!
    let liveMessages = first.messages
    liveMessages = reconcileToolEvent('session-a', liveMessages, frames[0])
    liveMessages = reconcileToolEvent('session-a', liveMessages, frames[1])
    const live = reconcileStreamChunk('session-a', liveMessages, first.cursor, 'after', afterTool)!

    expect(live.messages.map(message => message.role)).toEqual(['assistant', 'tool', 'assistant'])
    expect(live.messages.map(message => message.content)).toEqual(['before ', 'write_file', 'after'])
    expect(live.messages.map(message => message.streaming ?? false)).toEqual([false, false, true])
    expect(live.messages[1].meta).toMatchObject({ tool_call_id: 'call-1', done: true, output: 'saved' })
    expect(new Set(live.messages.map(message => message.id)).size).toBe(3)

    const hydrated: ChatDetail = { key: 'session-a', title: '', running: true, stream_cursor: beforeTool, messages: [
      { role: 'assistant', content: 'before ', meta: { ...beforeTool } },
      { role: 'tool', content: 'write_file', meta: { tool_call_id: 'call-1', done: false } },
    ] }
    let hydratedMessages = canonicalMessages(hydrated)
    hydratedMessages = reconcileToolEvent('session-a', hydratedMessages, frames[0])
    hydratedMessages = reconcileToolEvent('session-a', hydratedMessages, frames[1])
    const hydratedLive = reconcileStreamChunk('session-a', hydratedMessages, beforeTool, 'after', afterTool)!
    expect(hydratedLive.messages.map(message => message.role)).toEqual(['assistant', 'tool', 'assistant'])
    expect(hydratedLive.messages.map(message => message.content)).toEqual(['before ', 'write_file', 'after'])
    expect(new Set(hydratedLive.messages.map(message => message.id)).size).toBe(3)
  })

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
    let nativeHistoryGate: Promise<void> | undefined
    if (process.env.GIDEON_TEST_MODEL) {
      const trace = (stage: string, action: string, route = '') => {
        console.log('Native history gate', JSON.stringify({ at: new Date().toISOString(), stage, action, route }))
      }
      await command('Fetch.enable', { patterns: [{ urlPattern: `${origin}/api/chat/sessions/*`, requestStage: 'Request' }] })
      const heldRequests = new Map<'running' | 'streaming' | 'terminal', (requestId: string) => void>()
      const heldDetails = new Map<'running' | 'streaming' | 'terminal', Promise<string>>()
      for (const stage of ['running', 'streaming', 'terminal'] as const) {
        heldDetails.set(stage, new Promise<string>(done => { heldRequests.set(stage, done) }))
      }
      const capturedStages = new Set<'running' | 'streaming' | 'terminal'>()
      let requestNumber = 0
      const requestRoutes = new Map<string, string>()
      const sendFetchCommand = async (method: 'Fetch.continueRequest' | 'Fetch.disable', requestId?: string) => {
        const route = requestId ? requestRoutes.get(requestId) || 'unknown' : ''
        trace(route ? 'request' : 'fetch', `${method}:start`, route)
        await command(method, requestId ? { requestId } : {})
        trace(route ? 'request' : 'fetch', `${method}:complete`, route)
        if (requestId) requestRoutes.delete(requestId)
      }
      debuggerSocket!.addEventListener('message', event => {
        const frame = JSON.parse(String(event.data)) as { method?: string; params?: { requestId: string; request: { method: string; url: string } } }
        if (frame.method !== 'Fetch.requestPaused' || !frame.params) return
        const { requestId, request } = frame.params
        const number = ++requestNumber
        const route = request.url.includes('/approve') ? '/api/chat/sessions/:id/approve' : '/api/chat/sessions/:id'
        requestRoutes.set(requestId, `${number}:${request.method} ${route}`)
        trace('fetch', 'requestPaused', requestRoutes.get(requestId))
        if (request.method !== 'GET' || !request.url.includes('/api/chat/sessions/')) {
          void sendFetchCommand('Fetch.continueRequest', requestId)
          return
        }
        void evaluate('window.captureHistoryStage || ""').then(stage => {
          if ((stage === 'running' || stage === 'streaming' || stage === 'terminal') && !capturedStages.has(stage)) {
            capturedStages.add(stage)
            trace(stage, 'held', requestRoutes.get(requestId))
            heldRequests.get(stage)!(requestId)
          } else void sendFetchCommand('Fetch.continueRequest', requestId)
        }).catch(() => void sendFetchCommand('Fetch.continueRequest', requestId))
      })
      const awaitStage = async <T,>(stage: string, operation: () => Promise<T>): Promise<T> => {
        trace(stage, 'await:start')
        const value = await operation()
        trace(stage, 'await:complete')
        return value
      }
      nativeHistoryGate = (async () => {
        const runningRequest = await awaitStage('running:request', () => heldDetails.get('running')!)
        await awaitStage('running:approval-frame', () => evaluate("window.waitForConversationFrame('approval')"))
        await awaitStage('running:continue', () => sendFetchCommand('Fetch.continueRequest', runningRequest))
        await awaitStage('running:send-refresh', () => evaluate('window.sendPromise'))
        await awaitStage('streaming:arm', () => evaluate("window.captureHistoryStage = 'streaming'; window.releaseNativeApproval(); true"))
        const streamingRequest = await awaitStage('streaming:request', () => heldDetails.get('streaming')!)
        await awaitStage('terminal:arm', () => evaluate("window.captureHistoryStage = 'terminal'; true"))
        await awaitStage('streaming:continue', () => sendFetchCommand('Fetch.continueRequest', streamingRequest))
        const terminalRequest = await awaitStage('terminal:request', () => heldDetails.get('terminal')!)
        await awaitStage('terminal:chat-done', () => evaluate("window.waitForConversationFrame('chat_done')"))
        await awaitStage('terminal:continue', () => sendFetchCommand('Fetch.continueRequest', terminalRequest))
        await awaitStage('terminal:refresh-settled', () => evaluate('window.streamingRefresh'))
        await awaitStage('fetch:disable', () => sendFetchCommand('Fetch.disable'))
      })()
    }
    const result = await evaluate(`(async () => {
      const expectAnswer = ${Boolean(process.env.GIDEON_TEST_MODEL)}
      const owner = await window.signInOwner('conversation-owner', 'correct-horse-battery-staple')
      window.controller.setOwner(window.ownerScope(location.origin, owner))
      let session
      if (expectAnswer) {
        const createdResponse = await fetch('/api/chat/sessions', { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-Gideon-API-Version': '1', 'X-Session-Key': 'dashboard:ui' },
          body: JSON.stringify({ agent: 'conversation-test-agent' }) })
        const created = await createdResponse.json()
        if (!createdResponse.ok || !created.key) throw Error('native conversation creation failed: ' + JSON.stringify(created))
        session = created.key
        await window.controller.open(session)
      } else session = await window.controller.create()
      await new Promise((done, reject) => { let n=0; const timer=setInterval(() => { if (window.controller.snapshot().connected) { clearInterval(timer); done() } else if (++n > 100) { clearInterval(timer); reject(Error('socket did not connect')) } }, 50) })
      const socket = window.controller.socket
      const frames = []
      window.conversationFrames = frames
      const frameWaiters = new Map()
      window.waitForConversationFrame = type => new Promise(resolve => {
        const found = frames.find(frame => frame.type === type)
        if (found) { resolve(found); return }
        const waiters = frameWaiters.get(type) || []
        waiters.push(resolve)
        frameWaiters.set(type, waiters)
      })
      window.nativeApprovalPermit = new Promise(resolve => { window.releaseNativeApproval = resolve })
      socket.addEventListener('message', event => {
        const frame = JSON.parse(event.data)
        if (frame.data?.session === session || frame.type === 'approval_resolved') {
          const captured = { type: frame.type, role: frame.data.role || '', tool: frame.data.tool || '',
            tool_call_id: frame.data.tool_call_id || '', id: frame.data.id || '',
            approved: frame.data.approved, decision: frame.data.decision || '',
            content: typeof frame.data.content === 'string' ? frame.data.content : '',
            stream_epoch: frame.data.stream_epoch, stream_turn: frame.data.stream_turn,
            stream_seq: frame.data.stream_seq }
          frames.push(captured)
          for (const resolve of frameWaiters.get(frame.type) || []) resolve(captured)
          frameWaiters.delete(frame.type)
          if (frame.type === 'chat_chunk' && window.captureHistoryStage === 'streaming' && !window.streamingRefresh) {
            window.streamingRefresh = window.controller.refresh().then(() => {
              const read = [...window.nativeHistoryReads].reverse().find(item => item.detail.running
                && item.detail.stream_cursor
                && item.detail.messages.some(message => message.role === 'streaming'
                  && message.meta?.stream_epoch === item.detail.stream_cursor.stream_epoch
                  && message.meta?.stream_turn === item.detail.stream_cursor.stream_turn))
              const cursor = read?.detail.stream_cursor
              const nativeStream = cursor && read.detail.messages.find(message => message.role === 'streaming'
                && message.meta?.stream_epoch === cursor.stream_epoch
                && message.meta?.stream_turn === cursor.stream_turn)
              const stream = cursor && [...window.controller.snapshot().messages].reverse().find(message => message.streaming
                && message.meta?.stream_epoch === cursor.stream_epoch
                && message.meta?.stream_turn === cursor.stream_turn)
              if (!read || !cursor || !nativeStream || !stream) {
                window.activeOverlapEvidence = { runningSnapshot: Boolean(read), streamingMessage: Boolean(stream) }
                return
              }
              const queued = window.conversationFrames.filter(item => item.type === 'chat_chunk'
                && item.stream_epoch === cursor.stream_epoch && item.stream_turn === cursor.stream_turn
                && item.stream_seq > cursor.stream_seq).map(item => item.content).join('')
              window.activeOverlapEvidence = { content: stream.content,
                expected: nativeStream.content + queued, runningSnapshot: true, streamingMessage: true }
            })
          }
        }
      })
      const nativeFetch = window.fetch.bind(window)
      window.nativeHistoryReads = []
      window.fetch = async (input, init) => {
        const response = await nativeFetch(input, init)
        const url = typeof input === 'string' ? input : input.url
        const method = init?.method || (typeof input === 'string' ? 'GET' : input.method)
        if (method === 'GET' && url.includes('/api/chat/sessions/' + encodeURIComponent(session))) {
          const detail = await response.clone().json()
          window.nativeHistoryReads.push({ detail })
        }
        return response
      }
      const prompt = expectAnswer
        ? 'Use write_file to create native-event-output.txt in the current workspace with exactly the text conversation event checkpoint. Call only that tool, then after approval answer with these exact words: ready the native conversation event checkpoint is complete and the session history stays current while the response streams back to the owner.'
        : '  Reply with one word: ready.  '
      window.controller.setDraft(prompt)
      if (expectAnswer) {
        window.captureHistoryStage = 'running'
        window.sendPromise = window.controller.send()
      } else await window.controller.send()
      if (expectAnswer) {
        await new Promise((done, reject) => { let n=0; const timer=setInterval(() => {
          const pending = frames.find(frame => frame.type === 'approval')
          if (pending) { clearInterval(timer); done(pending) }
          else if (++n > 1800) { clearInterval(timer); reject(Error('native approval frame was not observed: ' + JSON.stringify(frames))) }
        }, 50) }).then(async pending => {
          await window.nativeApprovalPermit
          const approvalId = pending.id
          if (typeof approvalId !== 'string' || !approvalId) throw Error('native approval row has no canonical ID')
          const response = await fetch('/api/chat/sessions/' + encodeURIComponent(session) + '/approve', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-Gideon-API-Version': '1', 'X-Session-Key': 'dashboard:ui' },
            body: JSON.stringify({ action: 'approved', request_id: approvalId }),
          })
          const result = await response.json()
          if (!response.ok || !result.ok) throw Error('native approval resolution failed: ' + JSON.stringify(result))
        })
        await new Promise((done, reject) => { let n=0; const timer=setInterval(() => { if (frames.some(frame => frame.type === 'chat_done')) { clearInterval(timer); done() } else if (++n > 300) { clearInterval(timer); reject(Error('selected session completion not received: ' + JSON.stringify(frames))) } }, 50) })
        await window.sendPromise
        await window.controller.refresh()
        await new Promise((done, reject) => { let n=0; const timer=setInterval(() => {
          const state = window.controller.snapshot()
          const completedTool = state.messages.some(message => message.role === 'tool' && message.meta?.done === true)
          if (!state.running && state.phase === 'ready' && completedTool) { clearInterval(timer); done() }
          else if (++n > 300) { clearInterval(timer); reject(Error('terminal native history was not reconciled: ' + JSON.stringify(state))) }
        }, 50) })
      }
      const detail = await (await fetch('/api/chat/sessions/' + encodeURIComponent(session), { credentials: 'same-origin', headers: { 'X-Gideon-API-Version': '1', 'X-Session-Key': 'dashboard:ui' } })).json()
      await window.controller.refresh()
      const snapshot = window.controller.snapshot()
      const evidence = { session, connected: snapshot.connected, running: snapshot.running, phase: snapshot.phase,
        activeOverlapEvidence: window.activeOverlapEvidence,
        persisted: detail.messages.filter(m => m.role === 'user' && m.content.trim() === prompt.trim()).length,
        visible: snapshot.messages.filter(m => m.role === 'user' && m.content.trim() === prompt.trim()).length,
        assistantPersisted: detail.messages.filter(m => m.role === 'assistant' && m.content.trim()).map(m => m.content),
        assistantVisible: snapshot.messages.filter(m => m.role === 'assistant' && m.content.trim()).map(m => m.content),
        toolRows: detail.messages.filter(m => m.role === 'tool'), permissionRows: detail.messages.filter(m => m.role === 'permission'),
        liveLifecycle: snapshot.messages.filter(m => m.role === 'tool' || m.role === 'permission'),
        draft: snapshot.draft, ids: snapshot.messages.map(m => m.id) }
      if (expectAnswer) {
        await window.signOutOwner()
        window.controller.setOwner(null)
        return { ...evidence, frames, socketChanged: window.controller.socket !== socket,
          cleared: window.controller.snapshot() }
      }
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
    if (nativeHistoryGate) await nativeHistoryGate
    expect(result.session).toMatch(/^chat-/)
    expect(result.connected).toBe(true)
    expect(result.persisted).toBe(1)
    expect(result.visible).toBe(1)
    expect(result.draft).toBe('')
    expect(new Set(result.ids).size).toBe(result.ids.length)
    if (process.env.GIDEON_TEST_MODEL) {
      expect(result.frames.map((frame: { type: string }) => frame.type)).toContain('chat_chunk')
      expect(result.frames.map((frame: { type: string }) => frame.type)).toContain('tool_call')
      expect(result.frames.map((frame: { type: string }) => frame.type)).toContain('tool_result')
      expect(result.frames.map((frame: { type: string }) => frame.type)).toContain('approval')
      expect(result.frames.map((frame: { type: string }) => frame.type)).toContain('approval_resolved')
      expect(result.frames.map((frame: { type: string }) => frame.type)).toContain('chat_done')
      expect(result.frames).toContainEqual(expect.objectContaining({ type: 'chat_message', role: 'assistant' }))
      expect(result.assistantPersisted.length).toBeGreaterThan(0)
      expect(result.assistantVisible).toEqual(result.assistantPersisted)
      expect(result.activeOverlapEvidence).toMatchObject({ runningSnapshot: true, streamingMessage: true })
      expect(result.activeOverlapEvidence.content).toBe(result.activeOverlapEvidence.expected)
      expect(result.running).toBe(false)
      expect(result.phase).toBe('ready')
      expect(result.liveLifecycle.filter((message: { role: string }) => message.role === 'tool')).toHaveLength(result.toolRows.length)
      expect(result.toolRows).toEqual(expect.arrayContaining([expect.objectContaining({ role: 'tool', meta: expect.objectContaining({ done: true, output: expect.any(String) }) })]))
      expect(result.permissionRows).toEqual(expect.arrayContaining([expect.objectContaining({ role: 'permission', meta: expect.objectContaining({ resolved: 'approved' }) })]))
      expect(result.liveLifecycle).toEqual(expect.arrayContaining([
        expect.objectContaining({ role: 'tool', meta: expect.objectContaining({ done: true, output: expect.any(String) }) }),
        expect.objectContaining({ role: 'permission', meta: expect.objectContaining({ resolved: 'approved' }) }),
      ]))
      const writeCall = result.frames.find((frame: { type: string; tool: string }) => frame.type === 'tool_call' && frame.tool === 'write_file')
      expect(writeCall?.tool_call_id).toBeTruthy()
      expect(result.frames).toContainEqual(expect.objectContaining({ type: 'tool_result', tool_call_id: writeCall.tool_call_id }))
      const approval = result.frames.find((frame: { type: string; tool: string }) => frame.type === 'approval' && frame.tool === 'write_file')
      expect(approval?.id).toBeTruthy()
      expect(result.frames).toContainEqual(expect.objectContaining({ type: 'approval_resolved', id: approval.id, approved: true }))
      const evidenceDirectory = process.env.GIDEON_TEST_EVIDENCE_DIR
      if (evidenceDirectory) {
        await mkdir(evidenceDirectory, { recursive: true })
        await writeFile(join(evidenceDirectory, 'native-tool-approval.json'), JSON.stringify({
          sessionId: result.session,
          liveFrames: result.frames.filter((frame: { type: string }) => ['tool_call', 'tool_result', 'approval', 'approval_resolved'].includes(frame.type)),
          hydratedTools: result.toolRows.map((message: any) => ({ id: message.meta?.tool_call_id ?? message.id,
            done: message.meta?.done === true, ok: message.meta?.ok, hasOutput: typeof message.meta?.output === 'string' })),
          hydratedApprovals: result.permissionRows.map((message: any) => ({ id: message.meta?.approval_id ?? message.id,
            resolved: message.meta?.resolved ?? null })),
        }, null, 2) + '\n')
      }
    }
    console.log('Selected session websocket events:', result.frames.map((frame: { type: string }) => frame.type))
    console.log('Persisted assistant answer count:', result.assistantPersisted.length)
    expect(result.socketChanged).toBe(true)
    expect(result.cleared).toMatchObject({ sessionId: null, draft: '', messages: [], phase: 'signed-out' })
    if (process.env.GIDEON_TEST_MODEL) return
    expect(result.reconnected).toBe(1)
    expect(result.retainedEdit).toBe('Edited while sending')
    expect(result.failed).toEqual({ draft: 'Retain this draft', phase: 'failed' })

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
    expect(staleResult.state).toMatchObject({ draft: 'New owner send', error: '' })
    expect(['sending', 'recovering', 'failed']).toContain(staleResult.state.phase)
    expect(staleResult.state.sessionId).not.toBe(result.session)
    expect(staleResult.submitting).toBe(true)
    expect(newPostRequest).not.toBe('')
    await command('Fetch.failRequest', { requestId: newPostRequest, errorReason: 'ConnectionClosed' })
    const newerResult = await evaluate('(async () => { await window.newSend; return { state: window.controller.snapshot(), submitting: window.controller.submitting } })()')
    await command('Fetch.disable')
    expect(newerResult.state).toMatchObject({ draft: 'New owner send', phase: 'failed' })
    expect(newerResult.submitting).toBe(false)
  }, 120000)
})
