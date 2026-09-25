import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import WorldTravel from './WorldTravel'

let child: ChildProcess, home: string, origin: string, destination: string
const root = resolve(process.cwd(), '../..')
beforeAll(async () => {
  home = await mkdtemp(resolve(tmpdir(), 'gideon-world-travel-ui-'))
  child = spawn('/tmp/gideon-runtime-venv/bin/python', ['checks/runtime/capabilities/experience/serve_world_travel_ui.py', home], {
    cwd: root, env: { ...process.env, GIDEON_HOME: resolve(home, 'runtime'), PYTHONPATH: resolve(root, 'runtime'), PATH: '/tmp/gideon-world-engine-deps/bun-linux-x64:' + process.env.PATH }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  await new Promise<void>((accept, reject) => {
    let output = '', errors = ''
    child.stdout!.on('data', data => { output += String(data); const match = output.match(/ORIGIN_URL=(http:\/\/[^\s]+) DEST_URL=(http:\/\/[^\s]+)/); if (match) { origin = match[1] + '/api/capabilities/experience'; destination = match[2] + '/api/capabilities/experience'; accept() } })
    child.stderr!.on('data', data => { errors += String(data) })
    child.on('exit', code => reject(new Error(`HTTP process exited ${code}: ${errors}`)))
    child.on('error', reject)
  })
}, 30000)
afterAll(async () => {
  const stopped = new Promise<void>(resolve => child?.once('exit', () => resolve()))
  child?.kill(); await stopped; await rm(home, { recursive: true, force: true })
})

it('discovers a policy-allowed canonical peer and creates a real signed admission', async () => {
  location.hash = '/capabilities/experience?world=ui_lounge'
  render(<WorldTravel baseUrl={origin} world="ui_lounge" open />)
  expect(screen.queryByText('Actual destination')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Check travel destinations' }))
  const destinationButton = await screen.findByRole('button', { name: 'Visit Actual destination' })
  fireEvent.change(screen.getByLabelText('Guest name'), { target: { value: 'Console Visitor' } })
  fireEvent.click(destinationButton)
  const link = await screen.findByRole('link', { name: 'Enter Actual destination' })
  expect(link.getAttribute('href')).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/#\/capabilities\/experience\?guest=/)
  const ticket = new URL(link.getAttribute('href')!).hash.split('guest=')[1]
  const response = await fetch(`${destination}/world-travel/guest/${ticket}`)
  expect(response.status).toBe(200)
  expect(await response.json()).toMatchObject({ world: 'ui_lounge', guest_name: 'Console Visitor', state: 'active' })
})

it('renders only the ticket-scoped world and ends the durable visit', async () => {
  const departure = await fetch(`${origin}/world-travel/depart`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ peer_id: (await (await fetch(`${origin}/world-travel/destinations`)).json()).destinations[0].id, world: 'ui_lounge', guest_name: 'Guest View', request_id: 'ui_guest_view' }) }).then(response => response.json())
  const ticket = new URL(departure.url).hash.split('guest=')[1]
  location.hash = `/capabilities/experience?guest=${ticket}`
  render(<WorldTravel baseUrl={destination} world="ignored" open={false} />)
  expect(await screen.findByText(/Guest View is visiting ui_lounge/)).toBeVisible()
  expect(screen.getByTitle('Scoped guest world')).toHaveAttribute('src', `${destination}/world-travel/guest/${ticket}/host/?world=ui_lounge&guest=1&name=Guest%20View`)
  fireEvent.click(screen.getByRole('button', { name: 'Leave guest world' }))
  await waitFor(() => expect(screen.queryByTitle('Scoped guest world')).not.toBeInTheDocument())
  expect(location.hash).toBe('#/capabilities/experience')
  expect((await fetch(`${destination}/world-travel/guest/${ticket}`)).status).toBe(404)
})

it('keeps closed worlds and unavailable travel as explicit UI states', async () => {
  location.hash = '/capabilities/experience?world=ui_lounge'
  render(<WorldTravel baseUrl={origin} world="ui_lounge" open={false} />)
  fireEvent.click(screen.getByRole('button', { name: 'Check travel destinations' }))
  expect(await screen.findByRole('button', { name: 'Visit Actual destination' })).toBeDisabled()
  expect(screen.getByText('Open the local world before departing.')).toBeVisible()
  fireEvent.change(screen.getByLabelText('Guest name'), { target: { value: '' } })
  expect(screen.getByRole('button', { name: 'Visit Actual destination' })).toBeDisabled()
})
