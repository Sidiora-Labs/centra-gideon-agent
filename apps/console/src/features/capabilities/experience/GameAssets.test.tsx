import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import GameAssets from './GameAssets'

let child: ChildProcess, home: string, origin: string, token: string, spritePath: string
const nativeFetch = globalThis.fetch
function ownerFetch(input: RequestInfo | URL, init?: RequestInit) {
  const target = input instanceof Request ? input : new URL(String(input), origin)
  const url = target instanceof Request ? target.url : String(target)
  const headers = new Headers(target instanceof Request ? target.headers : undefined)
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
  if (new URL(url).origin === origin) headers.set('Authorization', `Bearer ${token}`)
  return nativeFetch(target, { ...init, headers })
}
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  home = await mkdtemp(resolve(tmpdir(), 'gideon-game-assets-ui-'))
  child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-m', 'checks.runtime.capabilities.experience.serve_game_assets_ui'], {
    cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  await new Promise<void>((done, reject) => {
    let output = '', errors = ''
    child.stdout!.on('data', chunk => { output += String(chunk); const line = output.split('\n').find(value => value.startsWith('{"url":')); if (line) { const ready = JSON.parse(line); origin = ready.url; token = ready.token; spritePath = ready.sprite_path; done() } })
    child.stderr!.on('data', chunk => { errors += String(chunk) })
    child.once('error', reject)
    child.once('exit', code => reject(new Error(`Native game compiler exited ${code}: ${errors}`)))
  })
  expect((await nativeFetch(origin + '/api/capabilities/experience/game-assets/projects')).status).toBe(403)
  expect(token).toBeTruthy()
  globalThis.fetch = ownerFetch
}, 30000)
afterEach(cleanup)
afterAll(async () => {
  globalThis.fetch = nativeFetch
  if (child?.exitCode === null && child.signalCode === null) { const stopped = new Promise<void>(done => child.once('exit', () => done())); child.kill(); await stopped }
  if (home) await rm(home, { recursive: true, force: true })
})
const projectsPath = '/api/capabilities/experience/game-assets/projects'
async function rowFor(title: string) {
  await screen.findByText(title)
  return screen.getAllByRole('listitem').find(row => within(row).queryByText(title))!
}
it('shows exact binding, compile and publication truth on the project row', async () => {
  render(<GameAssets baseUrl={origin + '/api/capabilities/experience'} />)
  const row = await rowFor('Runnable arcade')
  expect(screen.getByRole('status')).toHaveTextContent('3 game projects')
  expect(within(row).getByText('real-game-app')).toBeVisible()
  expect(within(row).getByText('4/4 assets')).toBeVisible()
  expect(within(row).getByText('compiled v1')).toBeVisible()
  expect(within(row).queryByText(/published/)).toBeNull()
  expect(within(row).getByRole('button', { name: 'Compile runnable export for Runnable arcade' })).toBeEnabled()
  expect(within(row).getByRole('button', { name: 'Publish Runnable arcade to its managed app' })).toBeEnabled()
  const project = (await (await ownerFetch(projectsPath)).json()).projects.find((value: { title: string }) => value.title === 'Runnable arcade')
  expect(project.compiled.version).toBe(1)
  expect(project.publication).toBeNull()
})
it('sends the durable revision and refreshes verified publication destination', async () => {
  const before = (await (await ownerFetch(projectsPath)).json()).projects.find((value: { title: string }) => value.title === 'Publication arcade')
  render(<GameAssets baseUrl={origin + '/api/capabilities/experience'} />)
  const row = await rowFor('Publication arcade')
  fireEvent.click(within(row).getByRole('button', { name: 'Publish Publication arcade to its managed app' }))
  await within(row).findByText('published verified')
  const after = (await (await ownerFetch(projectsPath)).json()).projects.find((value: { id: string }) => value.id === before.id)
  expect(after.revision).toBeGreaterThan(before.revision)
  expect(after.publication.state).toBe('verified')
  expect(after.publication.destination).toContain(before.id)
  expect(within(row).getByText('4/4 assets · ' + after.publication.destination)).toBeVisible()
  const stale = await ownerFetch(projectsPath + '/' + before.id + '/publish', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: before.revision }) })
  expect(stale.status).toBe(409)
})
it('surfaces compiler refusal and retains the last verified UI state', async () => {
  const before = (await (await ownerFetch(projectsPath)).json()).projects.find((value: { title: string }) => value.title === 'Integrity arcade')
  render(<GameAssets baseUrl={origin + '/api/capabilities/experience'} />)
  const row = await rowFor('Integrity arcade')
  expect(spritePath.startsWith(home + '/')).toBe(true)
  await writeFile(spritePath, 'corrupt')
  fireEvent.click(within(row).getByRole('button', { name: 'Compile runnable export for Integrity arcade' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(/corrupt|integrity/)
  expect(within(row).getByText('compiled v1')).toBeVisible()
  expect(within(row).queryByText(/published/)).toBeNull()
  await waitFor(() => expect(within(row).getByRole('button', { name: 'Compile runnable export for Integrity arcade' })).toBeEnabled())
  const after = (await (await ownerFetch(projectsPath)).json()).projects.find((value: { id: string }) => value.id === before.id)
  expect(after.compiled).toEqual(before.compiled)
  expect(after.publication).toBeNull()
  expect(after.revision).toBe(before.revision)
})
