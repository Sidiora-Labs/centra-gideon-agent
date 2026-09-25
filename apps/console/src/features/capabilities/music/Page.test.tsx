import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import Page from './Page'

let server: ChildProcess
let apiBase: string
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/music/serve_ui.py'], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  const port = await new Promise<string>((done, reject) => {
    let output = ''
    let errors = ''
    server.stderr!.on('data', chunk => { errors += String(chunk) })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP service exited ${code}: ${errors}`)))
    server.stdout!.on('data', chunk => {
      output += String(chunk)
      const number = output.split('\n').find(line => /^\d+$/.test(line))
      if (number) done(number)
    })
  })
  apiBase = `http://127.0.0.1:${port}/api/capabilities/music`
})
afterAll(() => { server?.kill('SIGTERM') })

it('authors a piece through real HTTP, practices, and reopens its persisted reader', async () => {
  window.location.hash = '#/capabilities/music'
  const page = render(<Page apiBase={apiBase} />)
  await screen.findByText('No repertoire yet. Add your first piece.')
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Evening canon' } })
  fireEvent.change(screen.getByLabelText('Instrument'), { target: { value: 'Guitar' } })
  fireEvent.change(screen.getByLabelText('Score or practice notes'), { target: { value: 'Em C G D\nRepeat softly' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add piece' }))
  const reader = await screen.findByRole('article', { name: 'Practice reader' })
  expect(within(reader).getByText('Evening canon')).toBeInTheDocument()
  expect(within(reader).getByText(/Em C G D/)).toBeInTheDocument()
  expect(within(reader).getByText(/Not scheduled/)).toBeInTheDocument()
  expect(window.location.hash).toMatch(/music\/[a-f0-9-]+$/)
  fireEvent.change(screen.getByLabelText('Practice grade'), { target: { value: '2' } })
  fireEvent.change(screen.getByLabelText('Practice timezone'), { target: { value: 'Europe/Berlin' } })
  fireEvent.click(screen.getByRole('button', { name: 'Log practice' }))
  await waitFor(() => expect(within(screen.getByRole('list', { name: 'Practice history' })).getAllByRole('listitem')).toHaveLength(1))
  expect(screen.getByText(/Grade 2 ·/)).toHaveTextContent('Europe/Berlin')
  expect(screen.getByText(/Stage: learning/)).toBeInTheDocument()
  const route = window.location.hash
  page.unmount()
  render(<Page apiBase={apiBase} />)
  await screen.findByRole('article', { name: 'Practice reader' })
  expect(screen.getByLabelText('Instrument')).toHaveValue('Guitar')
  expect(screen.getByLabelText('Score or practice notes')).toHaveValue('Em C G D\nRepeat softly')
  expect(screen.getByText(/Grade 2 ·/)).toBeInTheDocument()
  expect(window.location.hash).toBe(route)
})

it('shows a real optimistic conflict without discarding the unsaved text', async () => {
  const response = await fetch(apiBase + '/items', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: 'Concurrent piece' }) })
  const { item } = await response.json()
  window.location.hash = `#/capabilities/music/${item.id}`
  render(<Page apiBase={apiBase} />)
  await screen.findByRole('article', { name: 'Practice reader' })
  await fetch(apiBase + `/items/${item.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: 1, body: 'Saved elsewhere' }) })
  fireEvent.change(screen.getByLabelText('Score or practice notes'), { target: { value: 'My unsaved fingering' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Item changed; reload before editing')
  expect(screen.getByLabelText('Score or practice notes')).toHaveValue('My unsaved fingering')
  const reread = await fetch(apiBase + `/items/${item.id}`)
  expect((await reread.json()).item.body).toBe('Saved elsewhere')
})

it('reports missing canonical attachments from the real service', async () => {
  window.location.hash = '#/capabilities/music'
  render(<Page apiBase={apiBase} />)
  await screen.findByRole('button', { name: 'Add piece' })
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Missing score' } })
  fireEvent.change(screen.getByLabelText('Artifact attachments (slug@version)'), { target: { value: 'missing-score@1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add piece' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Attachment version not found')
  expect(screen.getByLabelText('Title')).toHaveValue('Missing score')
  expect(screen.queryByRole('article')).not.toBeInTheDocument()
})
