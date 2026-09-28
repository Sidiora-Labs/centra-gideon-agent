import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import type { ProjectionRule } from '../../shared/data/api'
import { ProjectionRulesPanel } from './ProjectionRulesPanel'

const RULE: ProjectionRule = { name: 'kept', match_regex: '^KEEP', strategy: 'json' }
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
  home = await mkdtemp(resolve(tmpdir(), 'gideon-projection-rule-ui-'))
  await mkdir(resolve(home, 'workspace'))
  await writeFile(resolve(home, 'config.json'), JSON.stringify({ tools: { projection_rules: [RULE] } }))
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-c', pythonServer], {
    cwd: root,
    env: { ...process.env, HOME: home, GIDEON_HOME: home, GIDEON_WORKSPACE: resolve(home, 'workspace'), PYTHONPATH: resolve(root, 'runtime') },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  origin = await new Promise<string>((accept, reject) => {
    let errors = ''
    server.stderr?.on('data', (chunk) => { errors += String(chunk) })
    server.once('error', reject)
    server.once('exit', (code) => reject(new Error(`Settings HTTP server exited ${code}: ${errors}`)))
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

describe('projection rule refusal', () => {
  it('keeps the add draft and existing rule when the real server refuses it', async () => {
    render(<ProjectionRulesPanel />)
    await screen.findByLabelText('Match regex for kept')
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('New rule name'), 'custom')
    fireEvent.change(screen.getByLabelText('Match regex for the new rule'), { target: { value: '([' } })
    await user.selectOptions(screen.getByLabelText('Strategy for the new rule'), 'test')
    await user.click(screen.getByRole('button', { name: 'Add rule' }))

    expect(await screen.findByRole('status')).toHaveTextContent(/regex/i)
    expect((screen.getByLabelText('New rule name') as HTMLInputElement).value).toBe('custom')
    expect((screen.getByLabelText('Match regex for the new rule') as HTMLInputElement).value).toBe('([')
    expect((screen.getByLabelText('Strategy for the new rule') as HTMLSelectElement).value).toBe('test')
    expect(screen.getByLabelText('Rule name')).toHaveValue('kept')
    const stored = await (await nativeFetch(origin + '/api/config/gideon')).json()
    expect(stored.tools.projection_rules).toEqual([{ ...RULE, count: '', head: 0, keep: '', skip: '', tail: 0 }])
  })
})
