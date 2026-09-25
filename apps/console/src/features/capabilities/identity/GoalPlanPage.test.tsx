import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'
import GoalPlanPage from './GoalPlanPage'

let server: ChildProcess
let endpoint = ''
let directory = ''
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  directory = await mkdtemp(resolve(tmpdir(), 'gideon-stories-ui-'))
  server = spawn('/tmp/gideon-runtime-venv/bin/python', [
    resolve(root, 'checks/runtime/capabilities/identity/goal_plans_ui_server.py'), directory,
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

test('human hierarchy, milestones and actual reported velocity persist through HTTP and reload', async () => {
  render(<GoalPlanPage endpoint={endpoint} />)
  await screen.findByRole('button', { name: 'Build telescope' })
  fireEvent.click(screen.getByRole('button', { name: 'Build telescope' }))
  const rows = await (await fetch(endpoint)).json()
  const parent = rows.find((row: { goal: { title: string } }) => row.goal.title === 'Learn astronomy')
  const child = rows.find((row: { goal: { title: string } }) => row.goal.title === 'Build telescope')
  expect(window.location.hash).toContain(child.goal.id)
  fireEvent.change(screen.getByLabelText('Parent goal'), { target: { value: parent.goal.id } })
  fireEvent.change(screen.getByLabelText('Metric unit'), { target: { value: 'pages' } })
  fireEvent.change(screen.getByLabelText('Target metric (optional)'), { target: { value: '100' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add milestone' }))
  fireEvent.change(screen.getByLabelText('Milestone title 1'), { target: { value: 'Read optics guide' } })
  fireEvent.click(screen.getByLabelText('Milestone 1 complete'))
  fireEvent.click(screen.getByRole('button', { name: 'Save goal plan' }))
  await waitFor(() => expect(screen.getByRole('region', { name: 'Goal plan summary' })).toHaveTextContent('Milestones reported complete: 100%'))
  expect(screen.getByRole('region', { name: 'Goal plan summary' })).toHaveTextContent('Human goal status: active')
  expect(screen.getByRole('region', { name: 'Goal plan summary' })).toHaveTextContent('Observed velocity: Unknown')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Record observation' })).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Observed value'), { target: { value: '10' } })
  fireEvent.change(screen.getByLabelText('Observed time with timezone'), { target: { value: '2026-10-01T10:00:00Z' } })
  fireEvent.change(screen.getByLabelText('Observation notes'), { target: { value: 'First chapter' } })
  fireEvent.click(screen.getByRole('button', { name: 'Record observation' }))
  await waitFor(() => expect(screen.getByRole('region', { name: 'Metric history' })).toHaveTextContent('10 pages'))
  expect(screen.getByRole('region', { name: 'Goal plan summary' })).toHaveTextContent('Observed velocity: Unknown')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Record observation' })).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Observed value'), { target: { value: '30' } })
  fireEvent.change(screen.getByLabelText('Observed time with timezone'), { target: { value: '2026-10-03T10:00:00Z' } })
  fireEvent.click(screen.getByRole('button', { name: 'Record observation' }))
  await waitFor(() => expect(screen.getByRole('region', { name: 'Goal plan summary' })).toHaveTextContent('Observed velocity: 10.000 pages/day'))
  const stored = await (await fetch(endpoint + '/' + child.goal.id)).json()
  expect(stored.plan.parent_id).toBe(parent.goal.id)
  expect(stored.plan.milestones[0].done).toBe(true)
  expect(stored.checkins).toHaveLength(2)
  expect(stored.checkins[0].source).toBe('human_reported')
  expect(stored.goal.status).toBe('active')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save goal plan' })).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Metric unit'), { target: { value: 'hours' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save goal plan' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('unit cannot change')
  expect(screen.getByLabelText('Metric unit')).toHaveValue('hours')
  fireEvent.click(screen.getByRole('button', { name: 'Reload goal plans' }))
  await waitFor(() => expect(screen.getByLabelText('Metric unit')).toHaveValue('pages'))
  fireEvent.click(screen.getByRole('button', { name: 'Learn astronomy' }))
  expect(screen.getByRole('link', { name: 'Build telescope' })).toBeVisible()
  fireEvent.change(screen.getByLabelText('Parent goal'), { target: { value: child.goal.id } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save goal plan' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Save goal plan' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('cycle')
  fireEvent.click(screen.getByRole('button', { name: 'Build telescope' }))
  cleanup()
  render(<GoalPlanPage endpoint={endpoint} />)
  await waitFor(() => expect(screen.getByLabelText('Metric unit')).toHaveValue('pages'))
  expect(screen.getByLabelText('Parent goal')).toHaveValue(parent.goal.id)
  expect(screen.getByLabelText('Milestone title 1')).toHaveValue('Read optics guide')
  expect(screen.getByLabelText('Milestone 1 complete')).toBeChecked()
  expect(screen.getByRole('region', { name: 'Metric history' })).toHaveTextContent('First chapter')
  expect(screen.getByRole('region', { name: 'Goal plan summary' })).toHaveTextContent('10.000 pages/day')
})
