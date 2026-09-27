import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { createPersonalClient, clearPersonalOwnerCache } from './client'
import { personalDetailRoute, personalSpaceRoute } from './routes'
import { parseShellRoute, serializeShellRoute } from '../../shared/shell/shellRoutes'
import { createShellRoute } from '../../shared/shell/shellRoutes'
import { personalModuleDefinitions } from './moduleDefinitions.web'
import { readIdentitySpace } from './PersonalHome'
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
  const python = `import asyncio, sys\nfrom pathlib import Path\nfrom aiohttp import web\nfrom gideon.interfaces.dashboard.handlers import capabilities_identity, capabilities_identity_goals, capabilities_identity_twin\nasync def main():\n app=web.Application()\n home=Path(sys.argv[2])\n capabilities_identity.register(app, store_path=home/'stories.sqlite3')\n capabilities_identity_goals.register(app, store_path=home/'goals.sqlite3')\n capabilities_identity_twin.register(app, store_path=home/'twin.sqlite3')\n runner=web.AppRunner(app); await runner.setup(); site=web.TCPSite(runner,'127.0.0.1',int(sys.argv[1])); await site.start()\n print('ready',flush=True)\n await asyncio.Event().wait()\nasyncio.run(main())`
  const child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-c', python, String(port), directory], {
    cwd: appRoot,
    env: { ...process.env, GIDEON_HOME: directory, PYTHONPATH: join(appRoot, 'runtime') },
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

  it('loads autobiography stories for its published route and the twin snapshot only on the twin route', async () => {
    const client = createPersonalClient({ ...scope, runtimeOrigin: nativeOrigin, cacheKey: JSON.stringify([nativeOrigin, scope.ownerId]) })
    const story = await client.saveIdentityStory(undefined, { prompt: 'What should Gideon know?', theme: 'continuity', text: 'A native autobiography record.' }, 'identity-create-001', 0)
    const autobiography = await readIdentitySpace(client, personalSpaceRoute('identity').placement?.id)
    expect(autobiography.state).toBe('available')
    if (autobiography.state === 'available') {
      expect(autobiography.value.kind).toBe('autobiography')
      if (autobiography.value.kind === 'autobiography') expect(autobiography.value.stories.map(item => item.id)).toContain(story.identity.nativeId)
    }

    const twin = await readIdentitySpace(client, 'capabilities/identity/twin')
    expect(twin.state).toBe('available')
    if (twin.state === 'available') {
      expect(twin.value.kind).toBe('twin')
      if (twin.value.kind === 'twin') expect(twin.value.profile).toHaveProperty('documents')
    }
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

  it('registers family routes only at the published personal placement IDs', () => {
    const ids = personalModuleDefinitions.map(module => module.id)
    expect(ids).toContain('ideas')
    expect(ids).toContain('goals')
    expect(ids).toContain('learning')
    expect(ids).toContain('companion')
    expect(ids).toContain('capabilities/identity/twin')
    expect(ids).toContain('capabilities/wellbeing/memory')
    expect(ids).toContain('capabilities/knowledge/journals')
    expect(new Set(ids).size).toBe(ids.length)
    for (const module of personalModuleDefinitions) {
      const route = createShellRoute(module.id === 'goals' ? 'goals' : 'ideas', { view: 'workspace', placement: { id: module.id } })
      expect(module.matches(route)).toBe(true)
    }
  })

  it('does not resolve unimplemented Identity and Health placements as ready', async () => {
    for (const id of ['capabilities/identity/fidelity', 'capabilities/wellbeing/labs']) {
      const module = personalModuleDefinitions.find(item => item.id === id)
      expect(module).toBeDefined()
      const route = createShellRoute('ideas', { view: 'workspace', placement: { id } })
      await expect(module!.resolve(scope, route)).resolves.toBe('unavailable')
    }
  })
})
