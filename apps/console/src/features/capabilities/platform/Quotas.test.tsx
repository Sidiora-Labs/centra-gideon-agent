import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

import Quotas from './Quotas'

let server: ChildProcess
let baseUrl = ''
let home = ''
let cycleStart = ''
let cycleEnd = ''
let expiry = ''

beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-quotas-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from datetime import datetime, timezone
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_platform_quotas import register
from gideon.operations.usage_ledger import TurnUsage, UsageJournal
async def main():
 home = Path(os.environ['GIDEON_HOME'])
 UsageJournal(home/'usage'/'turns.jsonl').append(TurnUsage(ts=datetime.now(timezone.utc).isoformat(),session_key='dashboard:quota',source='chat',agent='',provider='Work API',model='model-a',input_tokens=150,output_tokens=50,cost_usd=2.5,priced=True))
 app=web.Application(); register(app,home)
 runner=web.AppRunner(app); await runner.setup(); site=web.TCPSite(runner,'127.0.0.1',0); await site.start()
 print(site._server.sockets[0].getsockname()[1],flush=True); await asyncio.Event().wait()
asyncio.run(main())
`], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: home } })
  baseUrl = await new Promise<string>((accept, reject) => {
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP server exited ${code}`)))
    server.stdout!.once('data', data => accept(`http://127.0.0.1:${String(data).trim()}`))
    server.stderr!.on('data', data => { if (String(data).includes('Traceback')) reject(new Error(String(data))) })
  })
  const now = Date.now()
  cycleStart = new Date(now - 86_400_000).toISOString()
  cycleEnd = new Date(now + 20 * 86_400_000).toISOString()
  expiry = new Date(now + 3_600_000).toISOString()
})

afterAll(async () => {
  cleanup()
  if (server?.exitCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  rmSync(home, { recursive: true, force: true })
})

const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })

test('owner records real plan terms and sees canonical usage without invented provider evidence', async () => {
  const view = render(<Quotas baseUrl={baseUrl} />)
  await screen.findByRole('heading', { name: 'Record plan terms' })
  expect(screen.getByText(/Limits and plan prices are owner-configured/)).toBeInTheDocument()
  expect(screen.getByText(/Observed consumption comes from Gideon’s canonical local usage ledger/)).toBeInTheDocument()
  expect(screen.getByText(/Provider quota evidence appears only when a provider adapter supplied it/)).toBeInTheDocument()
  change('Provider binding', 'Work API')
  change('Plan name', 'Work subscription')
  change('Plan source', 'Owner copied provider plan page')
  change('Cycle start', cycleStart)
  change('Cycle end', cycleEnd)
  change('Configured token limit', '1000')
  change('Configured dollar limit', '10')
  change('Monthly plan cost', '20')
  fireEvent.click(screen.getByRole('button', { name: 'Save quota plan' }))
  const availability = await screen.findByRole('region', { name: 'Quota availability' })
  expect(within(availability).getByRole('heading', { name: 'Work subscription' })).toBeInTheDocument()
  expect(within(availability).getByText('Quota basis: user_configured. Usage basis: canonical_local_usage_ledger.')).toBeInTheDocument()
  expect(within(availability).getByRole('meter', { name: 'tokens used' })).toHaveAttribute('aria-valuetext', '200 of 1000 tokens used')
  expect(within(availability).getByText('$2.500000 recorded cost · 1 turns · 0 unpriced')).toBeInTheDocument()
  expect(within(availability).getByText('800 tokens available · $7.500000 available')).toBeInTheDocument()
  expect(within(availability).getByText(/does not grant provider entitlement or runtime admission/)).toBeInTheDocument()
  expect(within(availability).getByText(/no vendor quota is inferred/)).toBeInTheDocument()
  expect(within(availability).getByText('No provider-reported quota evidence.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Work subscription · Work API' })).toBeInTheDocument()
  view.unmount()
})

test('planning reservation visibly reduces configured availability and release restores it', async () => {
  const view = render(<Quotas baseUrl={baseUrl} />)
  const availability = await screen.findByRole('region', { name: 'Quota availability' })
  change('Run ID', 'quota-ui-run')
  change('Reservation purpose', 'Bounded local analysis')
  change('Expected tokens', '300')
  change('Expected dollars', '2')
  change('Reservation expiry', expiry)
  fireEvent.click(screen.getByRole('button', { name: 'Reserve capacity' }))
  const reservations = await screen.findByRole('region', { name: 'Quota reservations' })
  expect(within(reservations).getByText('quota-ui-run · Bounded local analysis · held · local_planning_only · 300 tokens · $2.000000')).toBeInTheDocument()
  expect(within(availability).getByText('300 reserved tokens · $2.000000 reserved · 1 active · 0 expired unreleased')).toBeInTheDocument()
  expect(within(availability).getByText('500 tokens available · $5.500000 available')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /admit/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /entitle/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /provider evidence/i })).not.toBeInTheDocument()
  change('Release reason', 'Work finished without more provider calls')
  fireEvent.click(within(reservations).getByRole('button', { name: 'Release quota-ui-run' }))
  await waitFor(() => expect(screen.getByText('quota-ui-run · Bounded local analysis · released · local_planning_only · 300 tokens · $2.000000')).toBeInTheDocument())
  expect(within(availability).getByText('0 reserved tokens · $0.000000 reserved · 0 active · 0 expired unreleased')).toBeInTheDocument()
  expect(within(availability).getByText('800 tokens available · $7.500000 available')).toBeInTheDocument()
  view.unmount()
})

test('overbooking error remains visible and does not create a reservation', async () => {
  const view = render(<Quotas baseUrl={baseUrl} />)
  await screen.findByRole('region', { name: 'Quota availability' })
  change('Run ID', 'too-large')
  change('Reservation purpose', 'Exceeds owner-configured ceiling')
  change('Expected tokens', '801')
  change('Expected dollars', '0')
  change('Reservation expiry', expiry)
  fireEvent.click(screen.getByRole('button', { name: 'Reserve capacity' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Configured token quota is exhausted')
  expect(screen.queryByText(/too-large ·/)).not.toBeInTheDocument()
  const response = await fetch(`${baseUrl}/api/capabilities/platform/quotas/plans`)
  const plans = await response.json()
  const held = await (await fetch(`${baseUrl}/api/capabilities/platform/quotas/plans/${plans.plans[0].id}/reservations`)).json()
  expect(held.reservations).toHaveLength(1)
  expect(held.reservations[0].status).toBe('released')
  view.unmount()
})
