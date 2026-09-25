import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { StrictMode } from 'react'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import SpokenNavigation from './SpokenNavigation'
import { resolveNavigation, type NavigationItem } from './navigation'

let child: ChildProcess
let home: string
let base: string
const root = resolve(process.cwd(), '../..')
const items = [{ id: 'projects', label: 'Projects' }, { id: 'apps/calendar', label: 'Team Calendar' }, { id: 'settings', label: 'Settings', disabled: true }]
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
function Shell({ catalog = items }: { catalog?: NavigationItem[] }) {
  const { route, sub, navigate } = useHashRoute('chat')
  return <><SpokenNavigation items={catalog} navigate={navigate} currentRoute={[route, sub].filter(Boolean).join('/')} baseUrl={base} /><div aria-label="Current route">{route}/{sub}</div></>
}
function enter(command: string) {
  fireEvent.change(screen.getByLabelText('Navigation command'), { target: { value: command } })
  fireEvent.click(screen.getByRole('button', { name: 'Go' }))
}
it('uses real shell routing and stores acknowledgement only after observed navigation in StrictMode', async () => {
  history.replaceState(null, '', '#/chat')
  render(<StrictMode><Shell /></StrictMode>)
  enter('Open Projects')
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Opened projects'))
  expect(location.hash).toBe('#/projects')
  expect(screen.getByLabelText('Current route')).toHaveTextContent('projects/')
  const { receipts } = await (await fetch(base + '/navigation')).json()
  expect(receipts[0].origin).toBe('chat')
  expect(receipts[0].target).toBe('projects')
  expect(receipts[0].observed_route).toBe('projects')
  expect(receipts[0].input_origin).toBe('typed')
  expect(receipts[0].revision).toBe(2)
  expect(screen.queryByRole('button', { name: 'Stop waiting' })).not.toBeInTheDocument()
})
it('accepts a live dynamically supplied app label and acknowledges the full subroute', async () => {
  history.replaceState(null, '', '#/chat')
  render(<Shell />)
  enter('go to Team Calendar!')
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Opened apps/calendar'))
  expect(location.hash).toBe('#/apps/calendar')
  expect(screen.getByLabelText('Current route')).toHaveTextContent('apps/calendar')
  const { receipts } = await (await fetch(base + '/navigation')).json()
  expect(receipts[0].target).toBe('apps/calendar')
  expect(receipts[0].status).toBe('applied')
})
it('rejects unknown and disabled commands without changing the real route or creating receipts', async () => {
  history.replaceState(null, '', '#/chat')
  const before = await (await fetch(base + '/navigation')).json()
  render(<Shell />)
  enter('Delete everything')
  expect(await screen.findByRole('alert')).toHaveTextContent('No destination matches')
  expect(location.hash).toBe('#/chat')
  enter('Settings')
  expect(await screen.findByRole('alert')).toHaveTextContent('unavailable')
  expect(location.hash).toBe('#/chat')
  expect(await (await fetch(base + '/navigation')).json()).toEqual(before)
})
it('resolves only the latest supplied catalog after a navigation item is disabled', async () => {
  history.replaceState(null, '', '#/chat')
  const mounted = render(<Shell />)
  mounted.rerender(<Shell catalog={[{ id: 'projects', label: 'Projects', disabled: true }]} />)
  enter('Projects')
  expect(await screen.findByRole('alert')).toHaveTextContent('unavailable')
  expect(location.hash).toBe('#/chat')
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})
it('reports actual missing browser microphone support without an invented transcript', async () => {
  render(<Shell />)
  fireEvent.click(screen.getByRole('button', { name: 'Use microphone' }))
  expect(await screen.findByRole('alert')).not.toBeEmptyDOMElement()
  expect(screen.getByLabelText('Navigation command')).toHaveValue('')
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Go' })).toBeDisabled()
})
it('normalizes displayed labels but refuses ambiguous labels and unsafe routes', () => {
  expect(resolveNavigation(' SHOW   team calendar. ', items).id).toBe('apps/calendar')
  expect(resolveNavigation('Cafe\u0301', [{ id: 'cafe', label: 'Café' }]).id).toBe('cafe')
  expect(() => resolveNavigation('Projects', [...items, { id: 'other', label: 'Projects' }])).toThrow('More than one')
  expect(resolveNavigation('Projects', [...items, { id: 'other', label: 'Projects', disabled: true }]).id).toBe('projects')
  for (const id of ['https://example.com', '../settings', '/settings', 'apps//one', 'apps/', 'a'.repeat(201)]) {
    expect(() => resolveNavigation('Destination', [{ id, label: 'Destination' }])).toThrow('local console')
  }
  expect(() => resolveNavigation('', items)).toThrow('available destination')
  expect(() => resolveNavigation('a'.repeat(401), items)).toThrow('available destination')
  expect(() => resolveNavigation('calendar', items)).toThrow('No destination')
})
