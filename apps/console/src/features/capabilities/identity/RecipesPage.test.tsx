import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import RecipesPage from './RecipesPage'

let server: ChildProcess
let endpoint = ''
let directory = ''
const root = resolve(process.cwd(), '../..')
const nativeFetch = globalThis.fetch
beforeAll(async () => {
  directory = await mkdtemp(resolve(tmpdir(), 'gideon-stories-ui-'))
  server = spawn(process.env.GIDEON_TEST_PYTHON || resolve(root, '.venv/bin/python'), [
    resolve(root, 'checks/runtime/capabilities/identity/recipes_ui_server.py'), directory,
  ], { env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: directory } })
  endpoint = await new Promise<string>((resolveEndpoint, reject) => {
    let output = ''
    let errors = ''
    const timer = setTimeout(() => reject(new Error('HTTP server startup timed out: ' + errors)), 10000)
    server.stderr?.on('data', chunk => { errors += String(chunk) })
    server.stdout?.on('data', chunk => {
      output += String(chunk)
      const line = output.split('\n').find(value => value.startsWith('{') && value.endsWith('}'))
      if (!line) return
      const ready = JSON.parse(line) as { port: number; token: string }
      const origin = `http://127.0.0.1:${ready.port}`
      globalThis.fetch = (input, init) => {
        const requestUrl = input instanceof Request ? input.url : String(input)
        const headers = new Headers(input instanceof Request ? input.headers : undefined)
        new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
        if (new URL(requestUrl, window.location.href).origin === origin) headers.set('Authorization', `Bearer ${ready.token}`)
        return nativeFetch(input, { ...init, headers })
      }
      clearTimeout(timer)
      resolveEndpoint(`${origin}/api/capabilities/identity/recipes`)
    })
    server.once('error', error => { clearTimeout(timer); reject(error) })
    server.once('exit', code => { clearTimeout(timer); reject(new Error('HTTP server exited ' + code + ': ' + errors)) })
  })
  const refused = await nativeFetch(endpoint)
  expect(refused.status).toBe(403)
  expect(await refused.json()).toMatchObject({ error: 'Token required' })
  expect((await fetch(endpoint)).status).toBe(200)
})
afterEach(() => { cleanup(); window.location.hash = '' })
afterAll(async () => {
  globalThis.fetch = nativeFetch
  if (server && server.exitCode === null) {
    await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
  }
  await rm(directory, { recursive: true, force: true })
})

test('author bound reads, execute actual operations, restore revisions and cancel pending run', async () => {
  render(<RecipesPage endpoint={endpoint} />)
  await screen.findByText('No recipes yet.')
  fireEvent.change(screen.getByLabelText('Recipe title'), { target: { value: 'Inspect telescope goal' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add read step' }))
  fireEvent.change(screen.getByLabelText('Read operation 2'), { target: { value: 'identity_goals_get_goal' } })
  fireEvent.change(screen.getByLabelText('Arguments and bindings 2 (JSON)'), { target: { value: JSON.stringify({ id: { $ref: 'first#/0/id' } }) } })
  fireEvent.click(screen.getByRole('button', { name: 'Save recipe' }))
  await screen.findByRole('button', { name: 'Inspect telescope goal · revision 1' })
  await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Begin pinned run' })))
  let recipes = await (await fetch(endpoint)).json()
  expect(recipes).toHaveLength(1)
  expect(recipes[0].steps).toHaveLength(2)
  expect(recipes[0].steps[1].arguments.id.$ref).toBe('first#/0/id')
  expect(window.location.hash).toContain(recipes[0].id)
  fireEvent.click(screen.getByRole('button', { name: 'Begin pinned run' }))
  await screen.findByRole('button', { name: /^Execute next read step for Inspect telescope goal, step [12]$/ })
  await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: /^Execute next read step for Inspect telescope goal, step [12]$/ })))
  fireEvent.click(screen.getByRole('button', { name: /^Execute next read step for Inspect telescope goal, step [12]$/ }))
  await screen.findByText('first: completed')
  let runs = await (await fetch(endpoint + '/runs')).json()
  expect(runs[0].status).toBe('ready')
  expect(runs[0].steps[0].output[0].title).toBe('Build telescope')
  await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: /^Execute next read step for Inspect telescope goal, step [12]$/ })))
  fireEvent.click(screen.getByRole('button', { name: /^Execute next read step for Inspect telescope goal, step [12]$/ }))
  await screen.findByText('step2: completed')
  expect(screen.getByRole('region', { name: 'Recipe runs' })).toHaveTextContent('2 steps recorded')
  runs = await (await fetch(endpoint + '/runs')).json()
  expect(runs[0].status).toBe('completed')
  expect(runs[0].steps[1].output.id).toBe(runs[0].steps[0].output[0].id)
  expect(screen.queryByRole('button', { name: /^Execute next read step for Inspect telescope goal, step [12]$/ })).not.toBeInTheDocument()
  await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Save recipe' })))
  fireEvent.change(screen.getByLabelText('Recipe title'), { target: { value: 'Changed title' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save recipe' }))
  await screen.findByRole('button', { name: 'Changed title · revision 2' })
  await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Restore revision 1' })))
  fireEvent.click(screen.getByRole('button', { name: 'Restore revision 1' }))
  await screen.findByRole('button', { name: 'Inspect telescope goal · revision 3' })
  expect(screen.getByRole('region', { name: 'Recipe history' })).toHaveTextContent('Revision 2: Changed title')
  await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Begin pinned run' })))
  fireEvent.click(screen.getByRole('button', { name: 'Begin pinned run' }))
  await screen.findByRole('button', { name: 'Cancel run for Inspect telescope goal, revision 3' })
  await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Cancel run for Inspect telescope goal, revision 3' })))
  fireEvent.click(screen.getByRole('button', { name: 'Cancel run for Inspect telescope goal, revision 3' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Cancel run for Inspect telescope goal, revision 3' })).not.toBeInTheDocument())
  runs = await (await fetch(endpoint + '/runs')).json()
  expect(runs.find((row: { status: string }) => row.status === 'cancelled').steps).toEqual([])
  cleanup()
  render(<RecipesPage endpoint={endpoint} />)
  await waitFor(() => expect(screen.getByLabelText('Recipe title')).toHaveValue('Inspect telescope goal'))
  expect(screen.getByLabelText('Arguments and bindings 2 (JSON)')).toHaveValue(JSON.stringify({ id: { $ref: 'first#/0/id' } }, null, 2))
  await waitFor(() => expectAvailableButton(screen.getByRole('button', { name: 'Save recipe' })))
  fireEvent.change(screen.getByLabelText('Arguments and bindings 1 (JSON)'), { target: { value: '{bad' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save recipe' }))
  await screen.findByRole('alert')
  expect(screen.getByLabelText('Arguments and bindings 1 (JSON)')).toHaveValue('{bad')
  recipes = await (await fetch(endpoint)).json()
  expect(recipes[0].revision).toBe(3)
})

function expectAvailableButton(button: HTMLElement) {
  expect(button).not.toBeDisabled()
  expect(button).not.toHaveAttribute('aria-disabled', 'true')
}
