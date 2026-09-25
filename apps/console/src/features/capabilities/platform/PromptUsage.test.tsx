import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { PromptDetail } from '../../prompts/PromptDetail'
import type { PromptItem } from '../../../shared/data/api'
import { PromptUsage, deletionBlockedReason, type PromptUsageRecord } from './PromptUsage'

let server: ChildProcess
let origin: string
let home: string
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-prompt-dependencies-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/prompt_ui_server.py'], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home, GIDEON_DEV_NO_AUTH: '1', GIDEON_SKIP_PROMPT_SEED: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let diagnostics = ''
  server.stderr!.on('data', chunk => { diagnostics += chunk.toString() })
  origin = await new Promise<string>((accept, reject) => {
    const lines = createInterface({ input: server.stdout! })
    lines.on('line', line => { if (/^\d+$/.test(line)) { accept(`http://127.0.0.1:${line}`); lines.close() } })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP process exited ${code}: ${diagnostics}`)))
  })
})
afterAll(async () => {
  if (server && server.exitCode === null) {
    await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  }
  await rm(home, { recursive: true, force: true })
})

async function request(path: string, method = 'GET', body?: unknown) {
  return fetch(`${origin}${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
}
type Detail = PromptItem & { usage: PromptUsageRecord }
function detailView(prompt: Detail, onDeleted: () => void) {
  return <><PromptUsage usage={prompt.usage} /><PromptDetail prompt={prompt} deletionBlockedReason={deletionBlockedReason(prompt.usage)} editing={false} onEditingChange={() => {}} onSaved={() => {}} onDeleted={onDeleted} onNavigate={() => {}} /></>
}

it('shows real binding consumer and blocks the existing delete control, then clears', async () => {
  const created = await request('/api/prompts', 'POST', { name: 'ui-dependent', content: 'Keep the original answer.', kind: 'user' })
  expect(created.status).toBe(200)
  const binding = await request('/api/prompts/bindings', 'PUT', { use_case: 'chat', ref: 'native:ui-dependent' })
  expect(binding.status).toBe(200)
  const result = await request('/api/prompts/ui-dependent')
  expect(result.status).toBe(200)
  const prompt = await result.json() as Detail
  let deleted = 0
  const view = render(detailView(prompt, () => { deleted++ }))
  expect(screen.getByRole('region', { name: 'Prompt dependencies' })).toHaveTextContent('Chat · binding · chat')
  expect(screen.getByText('Keep the original answer.')).toBeVisible()
  const control = screen.getByRole('button', { name: 'Delete' })
  expect(control).toHaveAttribute('aria-disabled', 'true')
  expect(control).toHaveAttribute('title', 'Remove active bindings or owning declarations before deleting this prompt.')
  fireEvent.click(control)
  expect(deleted).toBe(0)
  expect(screen.queryByRole('dialog')).toBeNull()
  expect((await request('/api/prompts/ui-dependent', 'DELETE')).status).toBe(409)
  const cleared = await request('/api/prompts/bindings', 'PUT', { use_case: 'chat', ref: '' })
  expect(cleared.status).toBe(200)
  const unbound = await (await request('/api/prompts/ui-dependent')).json() as Detail
  view.rerender(detailView(unbound, () => { deleted++ }))
  expect(screen.getByText('No active bindings or declared consumers.')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Delete' })).not.toHaveAttribute('aria-disabled', 'true')
  expect((await request('/api/prompts/ui-dependent', 'DELETE')).status).toBe(200)
  expect((await request('/api/prompts/ui-dependent')).status).toBe(404)
})

it('shows real unreadable binding state without pretending the prompt is missing', async () => {
  expect((await request('/api/prompts', 'POST', { name: 'ui-corrupt', content: 'Still present.' })).status).toBe(200)
  await writeFile(resolve(home, 'active_prompts.json'), '{')
  try {
    const response = await request('/api/prompts/ui-corrupt')
    expect(response.status).toBe(200)
    const prompt = await response.json() as Detail
    render(detailView(prompt, () => { throw new Error('Deletion must not occur') }))
    expect(screen.getByRole('status')).toHaveTextContent('Dependencies could not be read')
    expect(screen.getByText('Still present.')).toBeVisible()
    const control = screen.getByRole('button', { name: 'Delete' })
    expect(control).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(control)
    expect(screen.queryByRole('dialog')).toBeNull()
    const refused = await request('/api/prompts/ui-corrupt', 'DELETE')
    expect(refused.status).toBe(503)
    expect((await refused.json()).code).toBe('prompt_usage_unavailable')
  } finally { await writeFile(resolve(home, 'active_prompts.json'), '{}') }
})
