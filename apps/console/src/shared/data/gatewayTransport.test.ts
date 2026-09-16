// @vitest-environment node
import { createServer } from 'node:http'
import { createHash } from 'node:crypto'
import type { AddressInfo } from 'node:net'
import type { Duplex } from 'node:stream'
import { expect, it, vi } from 'vitest'
import { ApiError, gatewayHeaders, requestDelete, requestJson } from './gatewayRequest'
import { GatewaySocket, decodeSocketMessage } from './socketTransport'
import { configChangePath } from './useConfigFsWatch'

it('sends declared gateway headers and JSON bodies over a real HTTP connection', async () => {
  const requests: Array<{ method: string; session: string | string[] | undefined; body: string }> = []
  const server = createServer(async (request, response) => {
    let body = ''
    for await (const part of request) body += String(part)
    requests.push({ method: request.method!, session: request.headers['x-session-key'], body })
    response.setHeader('Content-Type', 'application/json')
    if (request.url === '/error') {
      response.statusCode = 409
      response.end(JSON.stringify({ error: { code: 'conflict', message: 'Already changed', detail: { revision: 2 } } }))
    } else if (request.method === 'DELETE') {
      response.statusCode = 204
      response.end()
    } else response.end(JSON.stringify({ accepted: JSON.parse(body || 'null') }))
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  try {
    expect(await requestJson(`${address}/write`, 'PATCH', { enabled: true })).toEqual({ accepted: { enabled: true } })
    await requestDelete(`${address}/delete`)
    const error = await requestJson(`${address}/error`).catch((failure: unknown) => failure)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 409, code: 'conflict', detail: { revision: 2 }, message: 'Already changed' })
    expect(requests[0]).toEqual({ method: 'PATCH', session: gatewayHeaders['X-Session-Key'], body: '{"enabled":true}' })
    expect(requests[1].method).toBe('DELETE')
  } finally { await new Promise<void>((resolve) => server.close(() => resolve())) }
})

it('shares one real WebSocket, fans out events and reconnects active observers', async () => {
  const sockets = new Set<Duplex>()
  let handshakes = 0
  const server = createServer()
  server.on('upgrade', (request, socket) => {
    const accept = createHash('sha1').update(String(request.headers['sec-websocket-key']) + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64')
    socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`)
    handshakes += 1
    sockets.add(socket)
    socket.on('close', () => sockets.delete(socket))
    socket.on('data', (data: Buffer) => { if ((data[0] & 0x0f) === 8) socket.end(Buffer.from([0x88, 0])) })
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const transport = new GatewaySocket(`ws://127.0.0.1:${(server.address() as AddressInfo).port}/api/ws`)
  const first: string[] = []
  const second: string[] = []
  const status: boolean[] = []
  let reconnects = 0
  const stopFirst = transport.attach({ message: (message) => first.push(message.type), status: (value) => status.push(value), reconnect: () => { reconnects += 1 } })
  const stopSecond = transport.attach({ message: (message) => second.push(message.type) })
  try {
    await vi.waitFor(() => expect(status).toEqual([true]))
    expect(handshakes).toBe(1)
    const payload = Buffer.from(JSON.stringify({ type: 'update', data: { ready: true } }))
    for (const socket of sockets) socket.write(Buffer.concat([Buffer.from([0x81, payload.length]), payload]))
    await vi.waitFor(() => expect([first, second]).toEqual([['update'], ['update']]))
    for (const socket of sockets) socket.destroy()
    await vi.waitFor(() => expect(reconnects).toBe(1))
    expect(handshakes).toBe(2)
    expect(status).toEqual([true, false, true])
  } finally {
    stopFirst()
    stopSecond()
    for (const socket of sockets) socket.destroy()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})

it('ignores malformed event envelopes and accepts valid configuration changes', () => {
  for (const invalid of ['{', 'null', '[]', '4', '{"type":2}']) expect(decodeSocketMessage(invalid)).toBeUndefined()
  expect(decodeSocketMessage('{"type":"done","data":{}}')).toEqual({ type: 'done', data: {} })
  expect(configChangePath('{"path":"agents/default.json"}')).toBe('agents/default.json')
  expect(configChangePath('{"path":3}')).toBeUndefined()
})
