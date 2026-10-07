import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { activeNavigationId, navigationItems, ROUTABLE_ROOTS } from '../../app/shell/navigationModel'
import { HypermidPage } from './HypermidPage'

describe('Hypermid native destination', () => {
  it('is an OSS route with a human-readable navigation entry', () => {
    const item = navigationItems(false).find(({ id }) => id === 'hypermid')
    expect(item?.label).toBe('Hypermid')
    expect(item?.section).toBe('More')
    expect(navigationItems(true).some(({ id }) => id === 'hypermid')).toBe(false)
    expect(ROUTABLE_ROOTS.has('hypermid')).toBe(true)
    expect(activeNavigationId('hypermid', '', {})).toBe('hypermid')
  })

  it('renders inside the native page shell with an accessible administration region', () => {
    const html = renderToStaticMarkup(<HypermidPage
      sub=""
      navigate={vi.fn()}
      navEpoch={0}
      query={{}}
      setQuery={vi.fn()}
    />)
    expect(html).toContain('Hypermid administration')
    expect(html).toContain('Hypermid status')
    expect(html).toContain('role="tablist"')
  })
})

it('offers configuration for the actual off runtime and preserves native failure evidence', async () => {
  const { spawn } = await import('node:child_process')
  const { mkdtemp, rm } = await import('node:fs/promises')
  const { tmpdir } = await import('node:os')
  const { resolve } = await import('node:path')
  const { render, screen, fireEvent, cleanup } = await import('@testing-library/react')
  const { resetDataStore } = await import('../../shared/data/data')
  const { HypermidPanel } = await import('../settings/HypermidPanel')
  const home = await mkdtemp(resolve(tmpdir(), 'gideon-hypermid-off-ui-'))
  const root = resolve(process.cwd(), '../..')
  const nativeFetch = globalThis.fetch
  const script = `
import asyncio, json, signal, time
from aiohttp import web
from gideon.core.config import AppConfig
from gideon.engine.gateway import RuntimeCoordinator
from gideon.engine.session import ConversationDirectory
from gideon.hypermid.lifecycle import build_runtime_lifecycle
from gideon.interfaces.dashboard.handlers.hypermid import register_hypermid_routes
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.security.approval_answer import OWNER, of_request

async def main():
    config = AppConfig()
    runtime = RuntimeCoordinator(config, no_dashboard=True, no_crons=True, no_open=True)
    lifecycle = build_runtime_lifecycle(runtime)
    await lifecycle.start()
    state = ConsoleState(ConversationDirectory(config), time.time())
    state.hypermid = lifecycle
    requests = []
    @web.middleware
    async def observe(request, handler):
        requests.append(request.path)
        return await handler(request)
    app = web.Application(middlewares=[token_auth_middleware(), observe])
    app['state'] = state
    register_hypermid_routes(app)
    async def observed(request):
        if of_request(request).kind != OWNER:
            raise web.HTTPForbidden()
        return web.json_response(requests)
    async def fail(request):
        if of_request(request).kind != OWNER:
            raise web.HTTPForbidden()
        lifecycle.adapter.mark_unavailable(OSError('Hypermid transport is unavailable'), code='RUNTIME_START_FAILED')
        return web.json_response({'ok': True})
    app.router.add_get('/control/requests', observed)
    app.router.add_post('/control/failure', fail)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    print(json.dumps({'url': 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1]), 'token': generate_token('hypermid-ui-owner')}), flush=True)
    stopped = asyncio.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(name, stopped.set)
    try:
        await stopped.wait()
    finally:
        await lifecycle.stop()
        await runner.cleanup()
asyncio.run(main())
`
  const server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-c', script], {
    cwd: root, env: { ...process.env, GIDEON_HOME: home, GIDEON_WORKSPACE: resolve(home, 'workspace'), PYTHONPATH: resolve(root, 'runtime') },
  })
  try {
    const ready = await new Promise<{ url: string; token: string }>((resolveReady, reject) => {
      let output = '', errors = ''
      const timer = setTimeout(() => reject(new Error('Native Hypermid fixture did not start: ' + errors)), 20_000)
      server.stderr.on('data', chunk => { errors += String(chunk) })
      server.stdout.on('data', chunk => {
        output += String(chunk)
        const line = output.split('\n').slice(0, -1).find(row => row.startsWith('{'))
        if (line) { clearTimeout(timer); resolveReady(JSON.parse(line)) }
      })
      server.once('error', error => { clearTimeout(timer); reject(error) })
      server.once('exit', code => { clearTimeout(timer); reject(new Error('Native Hypermid fixture exited ' + code + ': ' + errors)) })
    })
    const anonymous = await nativeFetch(ready.url + '/api/hypermid/overview')
    expect(anonymous.status).toBe(403)
    globalThis.fetch = (input, init) => {
      const address = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      const url = new URL(address, ready.url)
      const headers = new Headers(input instanceof Request ? input.headers : undefined)
      new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
      if (url.origin === ready.url) headers.set('Authorization', 'Bearer ' + ready.token)
      return nativeFetch(url, { ...init, headers })
    }
    const overview = await (await fetch(ready.url + '/api/hypermid/overview')).json()
    expect(overview).toMatchObject({ mode: 'off', availability: 'unavailable', daemon: { state: 'stopped' }, adapter: { full_host_integration: false }, store_health: 'unknown' })
    resetDataStore()
    render(<HypermidPanel />)
    await screen.findByText('Hypermid is off')
    expect(screen.getByText('stopped')).toBeVisible()
    expect(screen.getByText('Off')).toBeVisible()
    expect(screen.getByText('unknown')).toBeVisible()
    expect(screen.queryByRole('heading', { name: "Couldn't load your Hypermid sessions" })).not.toBeInTheDocument()
    const requests: string[] = await (await fetch(ready.url + '/control/requests')).json()
    expect(requests).not.toContain('/api/hypermid/sessions')
    expect(requests).not.toContain('/api/hypermid/memory')
    expect(requests).not.toContain('/api/hypermid/caches')
    fireEvent.click(screen.getByRole('button', { name: 'Configure Hypermid' }))
    expect(screen.getByRole('tab', { name: 'Configure' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: 'Configure Hypermid' })).toBeVisible()
    cleanup()
    const failure = await fetch(ready.url + '/control/failure', { method: 'POST' })
    expect(failure.status).toBe(200)
    resetDataStore()
    render(<HypermidPanel />)
    await screen.findByText('failed')
    expect(screen.queryByText('Hypermid is off')).not.toBeInTheDocument()
    await screen.findByRole('heading', { name: "Couldn't load your Hypermid sessions" })
    expect((await screen.findAllByText('Hypermid did not return an authoritative result.')).length).toBeGreaterThan(0)
    const failedRequests: string[] = await (await fetch(ready.url + '/control/requests')).json()
    expect(failedRequests).toContain('/api/hypermid/sessions')
  } finally {
    cleanup()
    resetDataStore()
    globalThis.fetch = nativeFetch
    if (server.exitCode === null && server.signalCode === null) {
      await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
    }
    await rm(home, { recursive: true, force: true })
  }
}, 30_000)
