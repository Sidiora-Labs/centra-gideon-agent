import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { join, resolve } from 'node:path'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import type { OwnerScope } from '../../shared/auth.web'
import { parseShellRoute, serializeShellRoute, type ShellReturnContext } from '../../shared/shell/shellRoutes'
import { GatewayError } from '../../shared/transport.web'
import WorkRoutes, { WORK_DESTINATIONS, createWorkRoute, findWorkDestination, workReturnRoute } from './WorkRoutes.web'
import { WorkClient, classifyWorkError, workEntry } from './workClient'
import { workModuleDefinitions } from './moduleDefinitions.web'

const scope: OwnerScope = Object.freeze({
  runtimeOrigin: 'http://127.0.0.1', ownerId: 'work-owner', cacheKey: '["work","work-owner"]',
})
const originalFetch = globalThis.fetch
let server: ChildProcessWithoutNullStreams | undefined
let api = ''
let taskId = ''
let projectId = ''

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3',
    [join(root, 'apps/assistant/test-support/work_server.py')], {
      env: { ...process.env, PYTHONPATH: join(root, 'runtime') },
    })
  const line = await new Promise<string>((resolveLine, rejectLine) => {
    let output = ''
    let errors = ''
    const timeout = setTimeout(() => rejectLine(new Error(`Work server timed out: ${errors}`)), 15000)
    server!.stdout.on('data', chunk => {
      output += String(chunk)
      if (output.includes('\n')) {
        clearTimeout(timeout)
        resolveLine(output.split('\n')[0])
      }
    })
    server!.stderr.on('data', chunk => { errors += String(chunk) })
    server!.once('exit', code => {
      clearTimeout(timeout)
      rejectLine(new Error(`Work server exited ${code}: ${errors}`))
    })
  })
  const ready = JSON.parse(line) as { port: number; task_id: string; project_id: string }
  api = `http://127.0.0.1:${ready.port}`
  taskId = ready.task_id
  projectId = ready.project_id
  globalThis.fetch = (input, init) => originalFetch(new URL(String(input), api), init)
}, 20000)

afterAll(() => {
  globalThis.fetch = originalFetch
  server?.kill('SIGTERM')
})

describe('Work routes', () => {
  it('keeps every named family and Skills/Tools destination deep-linkable with source context', () => {
    const origin: ShellReturnContext = {
      destination: 'chat', sessionId: 'conversation-12', selectionId: 'message-8', scrollY: 340,
    }
    const unique = new Set<string>()
    for (const destination of WORK_DESTINATIONS) {
      const key = `${destination.id}:${destination.subview ?? ''}`
      expect(unique.has(key)).toBe(false)
      unique.add(key)
      const route = createWorkRoute(destination.id, 'canonical-record', origin, destination.subview)
      expect(findWorkDestination(route)).toEqual(destination)
      expect(parseShellRoute(serializeShellRoute(route))).toEqual(route)
      expect(route.record).toEqual({ kind: destination.kind, id: 'canonical-record' })
      expect(route.returnTo).toEqual(origin)
      expect(workModuleDefinitions.some(module => module.matches(route))).toBe(true)
    }
    expect(new Set(workModuleDefinitions.map(module => module.id)).size).toBe(workModuleDefinitions.length)
    expect(unique.has('skills:/edit')).toBe(true)
    expect(unique.has('skills:/execution')).toBe(true)
    expect(unique.has('skills:/availability')).toBe(true)
    expect(unique.has('tools:/edit')).toBe(true)
    expect(unique.has('tools:/execution')).toBe(true)
    expect(unique.has('tools:/invoke')).toBe(true)
    expect(unique.has('tools:/availability')).toBe(true)
    expect(workReturnRoute(origin)).toMatchObject({ destination: 'chat', sessionId: 'conversation-12' })
  })

  it('uses the full-width shared frame for tool execution and keeps controls keyboard reachable', () => {
    const route = createWorkRoute('tools', '["native","inspect"]', undefined, '/invoke')
    const html = renderToStaticMarkup(<WorkRoutes route={route} scope={scope} navigate={() => {}} />)
    expect(html).toContain('data-workspace-mode="full"')
    expect(html).toContain('Run tool')
    expect(html).toContain('<button')
  })

  it('keeps tool provider and name together as the canonical identity', () => {
    const first = workEntry(scope, 'tool', { name: 'inspect', provider: 'native', description: '',
      disabled: false, providerDisabled: false })
    const second = workEntry(scope, 'tool', { name: 'inspect', provider: 'remote', description: '',
      disabled: false, providerDisabled: false })
    expect(first.identity.id).toBe('["native","inspect"]')
    expect(first.identity.id).not.toBe(second.identity.id)
    expect(first.title).toBe('inspect')
  })

  it('reads actual native task and project stores and preserves exact IDs', async () => {
    const client = new WorkClient(scope)
    const tasks = await client.list('task')
    expect(tasks.state).toBe('ready')
    if (!('value' in tasks)) throw new Error('Native tasks were unavailable')
    expect(tasks.value.some(row => row.identity.id === taskId && row.title === 'Source task')).toBe(true)
    const task = await client.detail('task', taskId)
    expect(task).toMatchObject({ state: 'ready', value: { identity: { kind: 'task', id: taskId } } })
    const project = await client.detail('project', projectId)
    expect(project).toMatchObject({ state: 'ready', value: { identity: { kind: 'project', id: projectId } } })
    expect(await client.detail('task', 'unknown-native-task')).toMatchObject({ state: 'unavailable' })
  })

  it('separates empty, stale, denied, unavailable and failed reads', async () => {
    const client = new WorkClient(scope)
    const response = await originalFetch(`${api}/api/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
    expect(response.ok).toBe(true)
    expect((await client.list('task')).state).toBe('empty')
    globalThis.fetch = () => Promise.reject(new Error('network disconnected'))
    expect((await client.list('task')).state).toBe('stale')
    expect((await client.list('project')).state).toBe('failed')
    expect(classifyWorkError(new GatewayError('forbidden', 403))).toBe('denied')
    expect(classifyWorkError(new GatewayError('unavailable', 503))).toBe('unavailable')
  })
})
