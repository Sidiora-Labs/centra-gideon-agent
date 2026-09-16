import { test, expect } from '@playwright/test'
import { createServer, type Server } from 'node:http'
import { createReadStream, existsSync, statSync } from 'node:fs'
import { extname, join, normalize } from 'node:path'
import { fileURLToPath } from 'node:url'
import { dirname } from 'node:path'


const DIST = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist')
const PORT = Number(process.env.PW_PWA_PORT || 10944)
const ORIGIN = `http://127.0.0.1:${PORT}`

const MIME: Record<string, string> = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
  '.json': 'application/json',
  '.webmanifest': 'application/manifest+json',
}

function serveDist(): { server: Server; ready: Promise<void> } {
  let calls = 0
  const server = createServer((req, res) => {
    const url = new URL(req.url || '/', ORIGIN)
    if (url.pathname === '/api/ping') {
      calls += 1
      res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
      res.end(JSON.stringify({ call: calls, secret: `payload-${calls}` }))
      return
    }
    const rel = url.pathname === '/' ? 'index.html' : normalize(url.pathname).replace(/^[/\\]+/, '')
    let file = join(DIST, rel)
    if (!existsSync(file) || !statSync(file).isFile()) file = join(DIST, 'index.html')
    res.writeHead(200, {
      'Content-Type': MIME[extname(file)] || 'application/octet-stream',
      'Cache-Control': 'no-store',
    })
    createReadStream(file).pipe(res)
  })
  const ready = new Promise<void>((resolve) => server.listen(PORT, '127.0.0.1', resolve))
  return { server, ready }
}

async function shutdown(server: Server): Promise<void> {
  server.closeAllConnections()
  await new Promise<void>((resolve) => server.close(() => resolve()))
}

test.describe('service worker', () => {
  test.describe.configure({ mode: 'serial' })

  test.skip(!existsSync(join(DIST, 'sw.js')), 'run `npm run build` first — dist/sw.js is required')

  test('serves the shell offline but NEVER serves /api from cache', async ({ page }) => {
    const { server, ready } = serveDist()
    await ready
    try {
      await page.goto(`${ORIGIN}/#/companion`)
      await page.evaluate(() => navigator.serviceWorker.ready)
      await page.reload()
      const controlled = await page.evaluate(() => navigator.serviceWorker.controller !== null)
      expect(controlled, 'the worker must control the page or nothing below is meaningful').toBe(
        true,
      )

      const first = await page.evaluate(() => fetch('/api/ping').then((r) => r.json()))
      const second = await page.evaluate(() => fetch('/api/ping').then((r) => r.json()))
      expect(first.call).toBe(1)
      expect(second.call).toBe(2)

      const cache = await page.evaluate(async () => {
        const names = await caches.keys()
        const store = await caches.open(names[0])
        const urls = (await store.keys()).map((r) => r.url)
        return {
          names,
          total: urls.length,
          apiKeys: urls.filter((u) => new URL(u).pathname.startsWith('/api')),
          shellMatches: (await store.match('/')) !== undefined,
          apiMatches: (await store.match('/api/ping')) !== undefined,
        }
      })
      expect(cache.names).toEqual([expect.stringContaining('gideon-shell-')])
      expect(cache.total, 'the worker precached nothing — check install()').toBeGreaterThan(0)
      expect(cache.shellMatches, 'the shell document is not retrievable from cache').toBe(true)
      expect(cache.apiKeys).toEqual([])
      expect(cache.apiMatches).toBe(false)

      await shutdown(server)
      await expect(async () => {
        await page.request.get(`${ORIGIN}/api/ping`)
      }).rejects.toBeTruthy()

      await page.reload()
      expect(await page.evaluate(() => document.querySelector('#root') !== null)).toBe(true)
      expect(await page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true)

      const offline = await page.evaluate(() =>
        fetch('/api/ping').then(
          (r) => r.text().then((body) => ({ ok: true as const, status: r.status, body })),
          (err) => ({ ok: false as const, error: String(err) }),
        ),
      )
      expect(offline.ok, `offline /api resolved instead of failing: ${JSON.stringify(offline)}`).toBe(
        false,
      )
      if (!offline.ok) expect(offline.error).toMatch(/Failed to fetch|NetworkError|network error/i)
      expect(JSON.stringify(offline)).not.toContain('payload-')
    } finally {
      await shutdown(server).catch(() => {
      })
    }
  })

  test('the manifest is installable and the worker is scoped to the origin root', async ({
    page,
    context,
  }) => {
    const { server, ready } = serveDist()
    await ready
    try {
      await page.goto(`${ORIGIN}/`)
      const registration = await page.evaluate(async () => {
        const reg = await navigator.serviceWorker.ready
        return { scope: reg.scope, hasActive: reg.active !== null }
      })
      expect(registration.scope).toBe(`${ORIGIN}/`)
      expect(registration.hasActive).toBe(true)

      const href = await page.getAttribute('link[rel=manifest]', 'href')
      expect(href).toBe('/manifest.webmanifest')
      const res = await page.request.get(`${ORIGIN}/manifest.webmanifest`)
      expect(res.headers()['content-type']).toContain('application/manifest+json')
      const manifest = (await res.json()) as { start_url: string; display: string; icons: unknown[] }
      expect(manifest.start_url).toBe('/#/companion')
      expect(manifest.display).toBe('standalone')
      expect(manifest.icons.length).toBeGreaterThan(0)

      const cdp = await context.newCDPSession(page)
      const appManifest = await cdp.send('Page.getAppManifest')
      expect(appManifest.errors, 'Chrome reported manifest parse errors').toEqual([])
      expect(appManifest.url).toContain('/manifest.webmanifest')
      const asChromeSawIt = JSON.parse(appManifest.data ?? '{}') as {
        display: string
        start_url: string
        icons: unknown[]
      }
      expect(asChromeSawIt.display).toBe('standalone')
      expect(asChromeSawIt.start_url).toBe('/#/companion')
      expect(asChromeSawIt.icons.length).toBeGreaterThan(0)
    } finally {
      await shutdown(server)
    }
  })
})
