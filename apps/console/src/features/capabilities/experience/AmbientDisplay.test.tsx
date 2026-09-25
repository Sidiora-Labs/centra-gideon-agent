import { spawn, execFile, type ChildProcess } from 'node:child_process'
import { promisify } from 'node:util'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import Page from './Page'
let child: ChildProcess
let home: string
let base: string
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  home = await mkdtemp(resolve(tmpdir(), 'gideon-navigation-ui-'))
  child = spawn('/tmp/gideon-runtime-venv/bin/python', ['checks/runtime/capabilities/experience/serve_ui.py', home], { cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  base = await new Promise<string>((accept, reject) => {
    let output = '', errors = ''
    child.stdout!.on('data', data => { output += String(data); if (output.includes('\n')) accept(output.trim() + '/api/capabilities/experience') })
    child.stderr!.on('data', data => { errors += String(data) })
    child.on('exit', code => reject(new Error(`HTTP process exited ${code}: ${errors}`)))
    child.on('error', reject)
  })
})
afterAll(async () => { child?.kill(); await rm(home, { recursive: true, force: true }) })


async function seed(title: string) {
  await promisify(execFile)('/tmp/gideon-runtime-venv/bin/python', ['-c', `import sys
from gideon.automation.triggers.store import TriggerStore
from gideon.automation.triggers.models import Trigger
TriggerStore().save_all([Trigger(id='ambient_schedule',name=sys.argv[1],kind='clock',spec={'kind':'cron','expr':'0 8 * * *'})])`, title], { cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') } })
}
async function openAmbient() {
  history.replaceState(null, '', '#/capabilities/experience')
  render(<Page baseUrl={base} />)
  fireEvent.click(screen.getByRole('button', { name: 'Open ambient display' }))
  await screen.findByLabelText('Ambient display')
  await screen.findByRole('button', { name: 'Save display preferences' })
}
it('opens from the actual story page route, reads live sources, and exits through Escape', async () => {
  await openAmbient()
  expect(location.hash).toBe('#/capabilities/experience?ambient=1')
  expect(screen.getByLabelText('Current time')).toHaveAttribute('dateTime')
  expect(screen.getByText('No recorded measurements; health values unknown.')).toBeInTheDocument()
  expect(screen.getByText('Mortality and life expectancy estimates are unavailable.')).toBeInTheDocument()
  expect(screen.getByText(/Progress profile is not configured/)).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Scheduled automations' })).toBeInTheDocument()
  fireEvent.keyDown(window, { key: 'Escape' })
  await screen.findByRole('heading', { name: 'Interactive stories' })
  expect(location.hash).toBe('#/capabilities/experience')
})
it('reports actual unavailable fullscreen API without claiming fullscreen entry, and touch exit works', async () => {
  await openAmbient()
  fireEvent.click(screen.getByRole('button', { name: 'Enter fullscreen' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Fullscreen is unavailable')
  expect(screen.queryByRole('button', { name: 'Fullscreen active' })).not.toBeInTheDocument()
  fireEvent.pointerDown(screen.getByLabelText('Ambient display'))
  fireEvent.click(screen.getByRole('button', { name: 'Exit ambient display' }))
  await screen.findByRole('heading', { name: 'Interactive stories' })
})
it('persists real display preferences across close and reopen', async () => {
  await openAmbient()
  fireEvent.click(screen.getByLabelText('Show clock'))
  fireEvent.change(screen.getByLabelText('Text size'), { target: { value: '2' } })
  fireEvent.change(screen.getByLabelText('Hide controls after seconds'), { target: { value: '60' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save display preferences' }))
  await waitFor(() => expect(screen.queryByLabelText('Current time')).not.toBeInTheDocument())
  expect(screen.getByLabelText('Ambient display').style.fontSize).toBe('2rem')
  const saved = await (await fetch(base + '/ambient')).json()
  expect(saved.preferences.show_clock).toBe(false)
  expect(saved.preferences.idle_seconds).toBe(60)
  fireEvent.click(screen.getByRole('button', { name: 'Exit ambient display' }))
  await screen.findByRole('button', { name: 'Open ambient display' })
  fireEvent.click(screen.getByRole('button', { name: 'Open ambient display' }))
  await screen.findByRole('button', { name: 'Save display preferences' })
  expect(screen.getByLabelText('Show clock')).not.toBeChecked()
  expect(screen.getByLabelText('Text size')).toHaveValue('2')
  expect(screen.queryByLabelText('Current time')).not.toBeInTheDocument()
})
it('reflects schedule changes made through the actual producer store on refresh', async () => {
  await seed('Morning routine')
  await openAmbient()
  const schedule = screen.getByRole('region', { name: 'Scheduled automations' })
  expect(within(schedule).getByText(/Morning routine/)).toBeInTheDocument()
  await seed('Evening routine')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh display' }))
  await within(schedule).findByText(/Evening routine/)
  expect(within(schedule).queryByText(/Morning routine/)).not.toBeInTheDocument()
})
it('surfaces optimistic preference conflicts from real HTTP without silently overwriting another display', async () => {
  await openAmbient()
  const before = await (await fetch(base + '/ambient')).json()
  const response = await fetch(base + '/ambient', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...before.preferences, font_scale: 3 }) })
  expect(response.status).toBe(200)
  fireEvent.click(screen.getByRole('button', { name: 'Save display preferences' }))
  await waitFor(() => expect(screen.getAllByRole('alert').some(node => node.textContent?.includes('preferences changed'))).toBe(true))
  expect((await (await fetch(base + '/ambient')).json()).preferences.font_scale).toBe(3)
  fireEvent.click(screen.getByRole('button', { name: 'Refresh display' }))
  await waitFor(() => expect(screen.getByLabelText('Text size')).toHaveValue('3'))
  fireEvent.change(screen.getByLabelText('Text size'), { target: { value: '2' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save display preferences' }))
  await waitFor(async () => expect((await (await fetch(base + '/ambient')).json()).preferences.font_scale).toBe(2))
})
