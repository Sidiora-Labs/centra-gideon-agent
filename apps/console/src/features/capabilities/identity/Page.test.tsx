import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { render, screen, waitFor, fireEvent, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import Page from './Page'

let server: ChildProcess
let endpoint = ''
let directory = ''
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  directory = await mkdtemp(resolve(tmpdir(), 'gideon-stories-ui-'))
  server = spawn('/tmp/gideon-runtime-venv/bin/python', [
    resolve(root, 'checks/runtime/capabilities/identity/ui_server.py'), resolve(directory, 'stories.sqlite3'),
  ], { env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') } })
  endpoint = await new Promise<string>((resolveEndpoint, reject) => {
    let output = ''
    let errors = ''
    const timer = setTimeout(() => reject(new Error('HTTP server startup timed out: ' + errors)), 10000)
    server.stderr?.on('data', chunk => { errors += String(chunk) })
    server.stdout?.on('data', chunk => {
      output += String(chunk)
      const line = output.split('\n').find(value => value.startsWith('http://'))
      if (line) { clearTimeout(timer); resolveEndpoint(line.trim()) }
    })
    server.once('error', error => { clearTimeout(timer); reject(error) })
    server.once('exit', code => { clearTimeout(timer); reject(new Error('HTTP server exited ' + code + ': ' + errors)) })
  })
})
afterEach(() => { cleanup(); window.location.hash = '' })
afterAll(async () => {
  if (server && server.exitCode === null) {
    await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
  }
  await rm(directory, { recursive: true, force: true })
})

async function fill(question: string, theme: string, answer: string) {
  const user = userEvent.setup()
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: question } })
  fireEvent.change(screen.getByLabelText('Theme'), { target: { value: theme } })
  fireEvent.change(screen.getByLabelText('Your answer'), { target: { value: answer } })
  return user
}

test('author, follow up, edit, export and delete through the actual HTTP application', async () => {
  const user = userEvent.setup()
  render(<Page endpoint={endpoint} />)
  await screen.findByText('No stories yet')
  await user.click(screen.getByRole('button', { name: 'Write your first story' }))
  expect(screen.getByRole('heading', { name: 'Your life stories' })).toBeVisible()
  await fill('A memorable teacher?', 'School', 'My teacher encouraged curiosity.')
  await user.click(screen.getByRole('button', { name: 'Save answer' }))
  await screen.findByRole('button', { name: 'Edit story' })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add follow-up' })).toBeEnabled())
  const parentId = new URLSearchParams(window.location.hash.split('?')[1]).get('story')
  expect(parentId).toHaveLength(32)
  expect(within(screen.getByRole('article')).getByText('My teacher encouraged curiosity.')).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Add follow-up' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save answer' })).toBeEnabled())
  expect(screen.getByText('Follow-up to A memorable teacher?')).toBeVisible()
  await fill('What changed afterwards?', 'School', 'I started asking questions.')
  await user.click(screen.getByRole('button', { name: 'Save answer' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add follow-up' })).toBeEnabled())
  const childId = new URLSearchParams(window.location.hash.split('?')[1]).get('story')
  expect(childId).not.toBe(parentId)
  const family = await (await fetch(endpoint + '/stories/' + childId + '/chain')).json()
  expect(family.map((story: { text: string }) => story.text)).toEqual(['My teacher encouraged curiosity.', 'I started asking questions.'])
  const chain = await screen.findByRole('region', { name: 'Story chain' })
  expect(within(chain).getByText('My teacher encouraged curiosity.')).toBeVisible()
  expect(within(chain).getByText('I started asking questions.')).toBeVisible()
  expect(within(screen.getByRole('article')).getByText('I started asking questions.')).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Edit story' }))
  fireEvent.change(screen.getByLabelText('Your answer'), { target: { value: 'I started asking better questions.' } })
  await user.click(screen.getByRole('button', { name: 'Save answer' }))
  await waitFor(() => expect(within(screen.getByRole('article')).getByText('I started asking better questions.')).toBeVisible())
  const historyResponse = await fetch(endpoint + '/stories/' + childId + '/history')
  expect(historyResponse.status).toBe(200)
  const history = await historyResponse.json()
  expect(history.map((row: { text: string }) => row.text)).toEqual(['I started asking questions.', 'I started asking better questions.'])
  const link = screen.getByRole('link', { name: 'Export chronology' })
  const exportResponse = await fetch(link.getAttribute('href')!)
  const exported = await exportResponse.json()
  expect(exported.stories.map((row: { id: string }) => row.id)).toEqual([parentId, childId])
  expect(exported.history).toHaveLength(3)
  await user.click(screen.getByRole('button', { name: 'Delete story' }))
  await waitFor(() => expect(screen.queryByRole('article')).not.toBeInTheDocument())
  expect(screen.queryByRole('region', { name: 'Story chain' })).not.toBeInTheDocument()
  expect(await (await fetch(endpoint + '/stories')).json()).toHaveLength(1)
})

test('URL selection reloads persisted answer and stale save preserves draft', async () => {
  const user = userEvent.setup()
  const created = await fetch(endpoint + '/stories', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: 'An important place?', theme: 'Home', text: 'The garden.', request_id: 'stale-ui' }) })
  expect(created.status).toBe(200)
  const parent = await created.json()
  window.location.hash = '#/capabilities/identity?story=' + parent.id
  render(<Page endpoint={endpoint} />)
  await screen.findByRole('button', { name: 'Edit story' })
  expect(within(screen.getByRole('article')).getByText(parent.text)).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Edit story' }))
  await waitFor(() => expect(screen.getByLabelText('Question')).toHaveValue(parent.prompt))
  expect(screen.getByLabelText('Your answer')).toHaveValue(parent.text)
  const changed = await fetch(endpoint + '/stories/' + parent.id, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: parent.prompt, theme: parent.theme, text: 'Edited elsewhere.', parent_id: null, expected_revision: parent.revision }) })
  expect(changed.status).toBe(200)
  fireEvent.change(screen.getByLabelText('Your answer'), { target: { value: 'Unsaved local answer.' } })
  await user.click(screen.getByRole('button', { name: 'Save answer' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Story changed; reload before saving')
  expect(screen.getByLabelText('Your answer')).toHaveValue('Unsaved local answer.')
  await user.click(screen.getByRole('button', { name: 'Reload stories' }))
  await waitFor(() => expect(screen.getByLabelText('Your answer')).toHaveValue('Edited elsewhere.'))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Cancel' }))
  await screen.findByRole('button', { name: 'Edit story' })
  expect(within(screen.getByRole('article')).getByText('Edited elsewhere.')).toBeVisible()
  fireEvent(window, new HashChangeEvent('hashchange'))
  expect(screen.getByRole('button', { name: 'Delete story' })).toBeEnabled()
})
