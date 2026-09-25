import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import RoundsPage from './RoundsPage'
let server: ChildProcess
let apiBase: string, catalogBase: string, selection: string, slug: string
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/music/serve_rounds.py'], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  const data = await new Promise<{port:number;selection:string;slug:string}>((done, reject) => {
    let output = '', errors = ''
    server.stderr!.on('data', chunk => { errors += String(chunk) })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`Rounds HTTP service exited ${code}: ${errors}`)))
    server.stdout!.on('data', chunk => {
      output += String(chunk)
      const line = output.split('\n').find(value => value.startsWith('{'))
      if (line) done(JSON.parse(line))
    })
  })
  apiBase = `http://127.0.0.1:${data.port}/api/capabilities/music/rounds`
  catalogBase = `http://127.0.0.1:${data.port}/api/capabilities/music/catalog`
  selection = data.selection
  slug = data.slug
})
afterAll(() => { server?.kill('SIGTERM') })
it('authors timed voice parts, pins a real recording and preserves saved practice after editing', async () => {
  window.location.hash = '#/capabilities/music/rounds'
  const page = render(<RoundsPage apiBase={apiBase} catalogBase={catalogBase} />)
  await screen.findByLabelText('Canon title')
  fireEvent.change(screen.getByLabelText('Canon title'), { target: { value: 'Evening voices' } })
  fireEvent.change(screen.getByLabelText('Tempo BPM'), { target: { value: '120' } })
  fireEvent.change(screen.getByLabelText('Part 1 name'), { target: { value: 'Lead voice' } })
  fireEvent.change(screen.getByLabelText('Part 1 notation'), { target: { value: 'D E F G' } })
  await screen.findByRole('option', { name: new RegExp('Actual voice recording') })
  fireEvent.change(screen.getByLabelText('Part 1 recording'), { target: { value: selection } })
  fireEvent.click(screen.getByRole('button', { name: 'Add voice part' }))
  fireEvent.change(screen.getByLabelText('Part 2 entry beat'), { target: { value: '4' } })
  expect(screen.getByText('Entry at 2.00 seconds')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Create canon' }))
  await screen.findByText('Practice uses saved arrangement revision 1.')
  expect(screen.getByLabelText('Play Lead voice')).toHaveAttribute('src', `/api/artifacts/${slug}/raw?version=1`)
  expect(screen.getByLabelText('Play Lead voice')).toHaveAttribute('controls')
  fireEvent.change(screen.getByLabelText('Practice grade'), { target: { value: '0' } })
  fireEvent.change(screen.getByLabelText('Practice notes'), { target: { value: 'Repeat the entrance' } })
  fireEvent.click(screen.getByRole('button', { name: 'Log part practice' }))
  await screen.findByText('Grade 0 · 2 parts · revision 1 · Repeat the entrance')
  fireEvent.change(screen.getByLabelText('Part 1 notation'), { target: { value: 'G F E D' } })
  expect(screen.getByRole('button', { name: 'Log part practice' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Save arrangement' }))
  await screen.findByText('Practice uses saved arrangement revision 2.')
  expect(screen.getByRole('button', { name: 'Log part practice' })).not.toBeDisabled()
  const id = window.location.hash.split('/rounds/')[1]
  const history = await (await fetch(apiBase + '/' + id + '/practice')).json()
  expect(history.items).toHaveLength(1)
  expect(history.items[0].part_snapshot[0].notation).toBe('D E F G')
  expect(history.items[0].grade).toBe(0)
  page.unmount()
  render(<RoundsPage apiBase={apiBase} catalogBase={catalogBase} />)
  await screen.findByText('Practice uses saved arrangement revision 2.')
  expect(screen.getByLabelText('Part 1 notation')).toHaveValue('G F E D')
  await screen.findByText('Grade 0 · 2 parts · revision 1 · Repeat the entrance')
})
it('keeps an unsaved arrangement visible after a real revision conflict', async () => {
  render(<RoundsPage apiBase={apiBase} catalogBase={catalogBase} />)
  await screen.findByLabelText('Canon title')
  const id = window.location.hash.split('/rounds/')[1]
  const current = (await (await fetch(apiBase + '/' + id)).json()).item
  const changed = await fetch(apiBase + '/' + id, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:current.revision,notes:'Concurrent edit'})})
  expect(changed.status).toBe(200)
  fireEvent.change(screen.getByLabelText('Canon title'), { target: { value: 'Unsaved title' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save arrangement' }))
  await screen.findByRole('alert')
  expect(screen.getByLabelText('Canon title')).toHaveValue('Unsaved title')
  expect(screen.getByRole('button', { name: 'Log part practice' })).toBeDisabled()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save arrangement' })).not.toBeDisabled())
  const persisted = (await (await fetch(apiBase + '/' + id)).json()).item
  expect(persisted.title).toBe('Evening voices')
  expect(persisted.notes).toBe('Concurrent edit')
})
