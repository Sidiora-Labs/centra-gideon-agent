import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { createPersonalClient, clearPersonalOwnerCache } from './client'
import { personalDetailRoute, personalSpaceRoute } from './routes'
import { parseShellRoute, serializeShellRoute } from '../../shared/shell/shellRoutes'
import type { OwnerScope } from '../../shared/auth.web'

const scope: OwnerScope = Object.freeze({ runtimeOrigin: 'http://127.0.0.1', ownerId: 'owner-a', cacheKey: JSON.stringify(['http://127.0.0.1', 'owner-a']) })
const nativeFetch = globalThis.fetch
const processes: ChildProcessWithoutNullStreams[] = []
const directories: string[] = []
let nativeOrigin = ''

async function freePort(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(ready => server.listen(0, '127.0.0.1', ready))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(closed => server.close(() => closed()))
  return port
}

beforeAll(async () => {
  const directory = await mkdtemp(join(tmpdir(), 'gideon-personal-native-'))
  directories.push(directory)
  const appRoot = resolve(process.cwd(), '../..')
  const port = await freePort()
  const python = `import asyncio, sys\nfrom pathlib import Path\nfrom aiohttp import web\nfrom gideon.interfaces.dashboard.handlers import capabilities_identity_goals\nasync def main():\n app=web.Application()\n capabilities_identity_goals.register(app, store_path=Path(sys.argv[2])/'goals.sqlite3')\n runner=web.AppRunner(app); await runner.setup(); site=web.TCPSite(runner,'127.0.0.1',int(sys.argv[1])); await site.start()\n print('ready',flush=True)\n await asyncio.Event().wait()\nasyncio.run(main())`
  const child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-c', python, String(port), directory], {
    cwd: appRoot,
    env: { ...process.env, PYTHONPATH: join(appRoot, 'runtime') },
  })
  processes.push(child)
  await new Promise<void>((ready, fail) => {
    let errors = ''
    const timeout = setTimeout(() => fail(new Error(`Native personal handler did not start: ${errors}`)), 15000)
    child.stdout.on('data', chunk => {
      if (String(chunk).includes('ready')) { clearTimeout(timeout); ready() }
    })
    child.stderr.on('data', chunk => { errors += String(chunk) })
    child.once('exit', code => { clearTimeout(timeout); fail(new Error(`Native personal handler exited ${code}: ${errors}`)) })
  })
  nativeOrigin = `http://127.0.0.1:${port}`
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => nativeFetch(new URL(String(input), nativeOrigin), init)) as typeof fetch
})

afterAll(async () => {
  globalThis.fetch = nativeFetch
  for (const child of processes) child.kill('SIGTERM')
  for (const directory of directories) await rm(directory, { recursive: true, force: true })
})

describe('personal native contracts', () => {
  it('creates and reads a human goal through the real native handler, retaining its canonical ID and revision', async () => {
    const client = createPersonalClient({ ...scope, runtimeOrigin: nativeOrigin, cacheKey: JSON.stringify([nativeOrigin, scope.ownerId]) })
    const created = await client.saveGoal(undefined, { title: 'Learn conversational French', description: 'Practice each week', status: 'active', target_date: null }, 'goal-create-001', 0)
    expect(created.identity).toEqual({ ownerScopeKey: JSON.stringify([nativeOrigin, scope.ownerId]), sourceKind: 'human-goal', nativeId: created.value.id })
    expect(created.revision).toBe(1)
    const listed = await client.readGoals()
    expect(listed.map(row => row.identity.nativeId)).toContain(created.identity.nativeId)
    const detail = await client.readGoal(created.identity.nativeId)
    expect(detail.value.title).toBe('Learn conversational French')
    expect(detail.revision).toBe(1)
    client.dispose()
  })

  it('invalidates an in-flight native response and clears owner-scoped personal records on transition', async () => {
    const client = createPersonalClient({ ...scope, runtimeOrigin: nativeOrigin, cacheKey: JSON.stringify([nativeOrigin, scope.ownerId]) })
    const pending = client.readGoals()
    clearPersonalOwnerCache(client.scope.cacheKey)
    await expect(pending).rejects.toThrow(/account changed/)
    client.dispose()
  })

  it('keeps canonical record selection, session, draft slot, and return destination in shell routes', () => {
    const chat = parseShellRoute('/assistant/chat?v=1&view=detail&recordKind=conversation&recordId=session-8&session=session-8')
    expect(chat.kind).toBe('route')
    if (chat.kind !== 'route') return
    const detail = personalDetailRoute('journal', 'journal-entry', 'journal-native-19', chat)
    const roundTrip = parseShellRoute(serializeShellRoute(detail))
    expect(roundTrip).toMatchObject({ destination: 'ideas', view: 'detail', record: { kind: 'journal-entry', id: 'journal-native-19' }, returnTo: { destination: 'chat', sessionId: 'session-8', selectionId: 'session-8' } })
    expect(personalSpaceRoute('goals').placement?.id).toBe('goals')
    expect(personalSpaceRoute('learning').placement?.id).toBe('learning')
  })
})
