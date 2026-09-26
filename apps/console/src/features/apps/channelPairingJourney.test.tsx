import { createServer, type Server } from 'node:http'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { AddressInfo } from 'node:net'
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { AppSummary } from '../../shared/data/api'
import { AppDetailPanel } from './AppsSection'

function manifestFor(name: string) {
  return JSON.parse(readFileSync(join(process.cwd(), '../../runtime/gideon/extensions/apps/native', name, 'app.json'), 'utf8')) as {
    name: string; displayName: string; description: string; version: string; icon: string
    provider: { settingsSchema: { properties: Record<string, unknown> } }
  }
}

function summaryFor(name: string): AppSummary {
  const manifest = manifestFor(name)
  return {
    name, displayName: manifest.displayName, description: manifest.description,
    version: manifest.version, icon: manifest.icon, enabled: true, native: true,
    origin: 'builtin', hasBackend: false, hasUI: false, uiPages: [],
    isProvider: true, providerType: 'channel',
    hasConfig: Object.keys(manifest.provider.settingsSchema.properties).length > 0,
    permissions: {}, tags: ['channel'], backendRunning: false, backendPort: null,
  }
}

async function serveChannel(name: string): Promise<{ server: Server; root: string; paired: () => void; writes: () => number }> {
  const manifest = manifestFor(name)
  const provider = name === 'weixin-channel' ? 'weixin' : 'whatsapp'
  const pairingCode = provider === 'whatsapp' ? '2@' + 'A'.repeat(1000) : 'weixin-pairing-code'
  let saved = false
  let connected = false
  let writeCount = 0
  let config: Record<string, unknown> = {}
  const server = createServer(async (request, response) => {
    response.setHeader('Content-Type', 'application/json')
    const path = request.url ?? ''
    if (path === `/api/apps/${name}/config` && request.method === 'GET') {
      response.end(JSON.stringify({ name, schema: manifest.provider.settingsSchema, config, _secret_set: [] }))
    } else if (path === `/api/apps/${name}/config` && request.method === 'PUT') {
      let body = ''
      for await (const part of request) body += part.toString()
      config = JSON.parse(body) as Record<string, unknown>
      writeCount++
      saved = true
      response.end(JSON.stringify({ ok: true, name, config }))
    } else if (path === `/api/channels/${provider}` && request.method === 'GET') {
      if (!saved) {
        response.statusCode = 404
        response.end(JSON.stringify({ error: 'unknown transport' }))
      } else {
        response.end(JSON.stringify({ name: provider, health: connected
          ? { state: 'ready', detail: 'Connected' }
          : { state: 'pairing', detail: 'Scan the pairing QR', pairingQr: pairingCode } }))
      }
    } else {
      response.statusCode = 404
      response.end(JSON.stringify({ error: 'unknown route' }))
    }
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const port = (server.address() as AddressInfo).port
  return { server, root: `http://127.0.0.1:${port}`, paired: () => { connected = true }, writes: () => writeCount }
}

describe('native channel pairing in Apps', () => {
  it.each([
    ['weixin-channel', 'WeChat'],
    ['whatsapp-channel', 'WhatsApp'],
  ])('%s exposes Configure before credentials and keeps QR status live after Save', async (name, label) => {
    const peer = await serveChannel(name)
    const originalFetch = globalThis.fetch
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
      originalFetch(new URL(String(input), peer.root), init)) as typeof fetch
    try {
      render(<AppDetailPanel app={summaryFor(name)} onClose={() => {}} onChanged={() => {}} onOpen={() => {}} />)
      fireEvent.click(screen.getByRole('button', { name: /Configure/ }))
      const dialog = await screen.findByRole('dialog')
      expect(dialog).toHaveTextContent(`Configure ${label === 'WeChat' ? 'Personal WeChat' : label}`)
      const save = await screen.findByRole('button', { name: 'Save' })
      await waitFor(() => {
        expect(save).not.toBeDisabled()
        expect(save).not.toHaveAttribute('aria-disabled', 'true')
      })
      fireEvent.click(save)
      await waitFor(() => expect(peer.writes()).toBe(1))
      const qr = await screen.findByRole('img', { name: new RegExp(`Scan this QR with ${label}`) })
      expect(qr.querySelector('path')?.getAttribute('d')).toBeTruthy()
      if (name === 'whatsapp-channel') expect(Number(qr.style.width.replace('px', ''))).toBeGreaterThanOrEqual(300)
      expect(screen.getByRole('dialog')).toBeInTheDocument()
      peer.paired()
      await waitFor(() => expect(screen.queryByRole('img', { name: /Scan this QR/ })).toBeNull(), { timeout: 5000 })
      expect(screen.getByRole('status')).toHaveTextContent('Connected')
    } finally {
      globalThis.fetch = originalFetch
      await new Promise<void>((resolve, reject) => peer.server.close((error) => error ? reject(error) : resolve()))
    }
  })
})
