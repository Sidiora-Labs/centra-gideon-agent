import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdir, mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { VocabularySection } from './VoicePanel'
import { DialogHost } from '../../shared/ui/dialog/DialogHost'

const root = process.env.GIDEON_TEST_ROOT || resolve(process.cwd(), '../..')
const nativeFetch = globalThis.fetch
let home: string
let server: ChildProcess
let origin: string

const pythonServer = `import asyncio
from aiohttp import web
from gideon.interfaces.dashboard.handlers.core import api_gideon_config, api_gideon_config_patch
from gideon.cognition.lexicon.handlers import register_lexicon_routes
app = web.Application()
app.router.add_get('/api/config/gideon', api_gideon_config)
app.router.add_patch('/api/config/gideon', api_gideon_config_patch)
register_lexicon_routes(app)
async def main():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    print(site._server.sockets[0].getsockname()[1], flush=True)
    await asyncio.Event().wait()
asyncio.run(main())`

beforeAll(async () => {
  home = await mkdtemp(resolve(tmpdir(), 'gideon-lexicon-ui-'))
  await mkdir(resolve(home, 'workspace'))
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-c', pythonServer], {
    cwd: root,
    env: { ...process.env, HOME: home, GIDEON_HOME: home, GIDEON_WORKSPACE: resolve(home, 'workspace'), PYTHONPATH: resolve(root, 'runtime') },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  origin = await new Promise<string>((accept, reject) => {
    let errors = ''
    server.stderr?.on('data', (chunk) => { errors += String(chunk) })
    server.once('error', reject)
    server.once('exit', (code) => reject(new Error(`Lexicon HTTP server exited ${code}: ${errors}`)))
    server.stdout?.on('data', (chunk) => {
      const port = String(chunk).trim().split('\n').find((line) => /^\d+$/.test(line))
      if (port) accept(`http://127.0.0.1:${port}`)
    })
  })
  globalThis.fetch = (input, init) => nativeFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, init)
})

afterEach(() => cleanup())
afterAll(async () => {
  globalThis.fetch = nativeFetch
  if (server && server.exitCode === null) {
    const stopped = new Promise<void>((done) => server.once('exit', () => done()))
    server.kill('SIGTERM')
    await stopped
  }
  if (home) await rm(home, { recursive: true, force: true })
})

async function post(path: string, body: unknown) {
  const response = await nativeFetch(origin + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  expect(response.status).toBe(200)
  return response.json()
}

describe('learned correction deletion through the real lexicon route', () => {
  it('confirms and removes one correction while keeping neighboring vocabulary', async () => {
    const first = await post('/api/lexicon/corrections', { heard: 'Niro', meant: 'Nero' })
    await post('/api/lexicon/corrections', { heard: 'Gideo', meant: 'Gideon' })
    await post('/api/lexicon/terms', { canonical: 'Kubernetes' })
    const termsBefore = await (await nativeFetch(origin + '/api/lexicon/terms')).json()
    render(<><VocabularySection scrollTo={false}/><DialogHost/></>)

    await screen.findByText('Niro')
    await userEvent.click(screen.getByRole('button', { name: 'Delete correction Niro to Nero' }))
    expect(await screen.findByText(/Delete learned correction/)).toBeTruthy()
    await userEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(screen.queryByText('Niro')).toBeNull())
    expect(screen.getByText('Gideo', { exact: true })).toBeTruthy()
    const terms = await (await nativeFetch(origin + '/api/lexicon/terms')).json()
    const corrections = await (await nativeFetch(origin + '/api/lexicon/corrections')).json()
    expect(terms.terms.map((term: { canonical: string }) => term.canonical)).toEqual(termsBefore.terms.map((term: { canonical: string }) => term.canonical))
    expect(corrections.corrections.map((row: { heard: string }) => row.heard)).toEqual(['Gideo'])
    expect(first.ok).toBe(true)
  })

  it('announces a real 404 when another client already removed the correction', async () => {
    const created = await post('/api/lexicon/corrections', { heard: 'Niro', meant: 'Nero' })
    const listed = await (await nativeFetch(origin + '/api/lexicon/corrections')).json()
    const correction = listed.corrections.find((row: { heard: string }) => row.heard === 'Niro')
    render(<><VocabularySection scrollTo={false}/><DialogHost/></>)

    await screen.findByText('Niro')
    const external = await nativeFetch(`${origin}/api/lexicon/corrections/${correction.id}`, { method: 'DELETE' })
    expect(external.status).toBe(200)
    await userEvent.click(screen.getByRole('button', { name: 'Delete correction Niro to Nero' }))
    await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Delete' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('correction not found')
    expect(created.ok).toBe(true)
  })
})
