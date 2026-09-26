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
  home = mkdtempSync(join(tmpdir(), 'gideon-quota-donor-'))
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

async function createPlan(name: string, limit: string) {
  const view = render(<Quotas baseUrl={baseUrl} />)
  await screen.findByRole('heading', { name: 'Record plan terms' })
  change('Provider binding', 'Work API')
  change('Plan name', name)
  change('Plan source', 'Owner copied provider plan page')
  change('Cycle start', cycleStart)
  change('Cycle end', cycleEnd)
  change('Configured token limit', limit)
  change('Configured dollar limit', '10')
  change('Monthly plan cost', '20')
  fireEvent.click(screen.getByRole('button', { name: 'Save quota plan' }))
  const availability = await screen.findByRole('region', { name: 'Quota availability' })
  await waitFor(() => expect(within(availability).getByRole('heading', { name })).toBeInTheDocument())
  return { view, availability }
}

test('configured donor meter uses actual ledger tokens and keeps local reservations separate', async () => {
  const { view, availability } = await createPlan('Measured plan', '1000')
  const meter = within(availability).getByRole('meter', { name: 'tokens used' })
  expect(meter).toHaveAttribute('aria-valuetext', '200 of 1000 tokens used')
  expect(meter).toHaveAttribute('aria-valuenow', '20')
  expect((meter.firstElementChild as HTMLElement).style.width).toBe('20%')
  expect(within(availability).getByText('800 tokens unconsumed before reservations')).toBeInTheDocument()
  const plans = await (await fetch(`${baseUrl}/api/capabilities/platform/quotas/plans`)).json() as { plans: Array<{ name: string; cycle_end: string }> }
  const savedPlan = plans.plans.find(plan => plan.name === 'Measured plan')
  expect(savedPlan).toBeDefined()
  expect(within(availability).getByText(`resets on ${savedPlan!.cycle_end}`)).toBeInTheDocument()
  expect(within(availability).queryByText(/200 observed tokens/)).toBeNull()
  expect(within(availability).getByText('$2.500000 recorded cost · 1 turns · 0 unpriced')).toBeInTheDocument()
  expect(within(availability).getByText('No provider-reported quota evidence.')).toBeInTheDocument()
  change('Run ID', 'donor-hold')
  change('Reservation purpose', 'Local capacity planning')
  change('Expected tokens', '300')
  change('Expected dollars', '2')
  change('Reservation expiry', expiry)
  fireEvent.click(screen.getByRole('button', { name: 'Reserve capacity' }))
  await waitFor(() => expect(within(availability).getByText('500 tokens available · $5.500000 available')).toBeInTheDocument())
  expect(within(availability).getByText('300 reserved tokens · $2.000000 reserved · 1 active · 0 expired unreleased')).toBeInTheDocument()
  expect(within(availability).getByText('800 tokens unconsumed before reservations')).toBeInTheDocument()
  view.unmount()
})

test('an owner plan without a token ceiling keeps unknown capacity explicit and omits the donor meter', async () => {
  const { view, availability } = await createPlan('Uncapped plan', '')
  expect(within(availability).queryByRole('meter', { name: 'tokens used' })).toBeNull()
  expect(availability.querySelector('[data-slot="quota-banner"]')).toBeNull()
  expect(within(availability).getByText('No configured token ceiling · $7.500000 available')).toBeInTheDocument()
  expect(within(availability).getByText('$2.500000 recorded cost · 1 turns · 0 unpriced')).toBeInTheDocument()
  expect(within(availability).getByText('No provider-reported quota evidence.')).toBeInTheDocument()
  view.unmount()
})
