import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import IntegrationApps from './IntegrationApps'

let server: ChildProcess
let baseUrl: string
let home: string

beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-integration-apps-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/integration_apps_ui_server.py'], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home, GIDEON_DEV_NO_AUTH: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let diagnostics = ''
  server.stderr!.on('data', chunk => { diagnostics += chunk.toString() })
  baseUrl = await new Promise<string>((accept, reject) => {
    const lines = createInterface({ input: server.stdout! })
    lines.on('line', line => {
      if (/^\d+$/.test(line)) {
        accept(`http://127.0.0.1:${line}`)
        lines.close()
      }
    })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP process exited ${code}: ${diagnostics}`)))
  })
})

afterAll(async () => {
  if (server && server.exitCode === null) {
    await new Promise<void>(done => {
      server.once('exit', () => done())
      server.kill('SIGTERM')
    })
  }
  await rm(home, { recursive: true, force: true })
})

async function saveJira() {
  fireEvent.change(screen.getByLabelText('Integration label'), { target: { value: 'Delivery Jira' } })
  fireEvent.change(screen.getByLabelText('Integration API origin'), { target: { value: 'https://delivery.atlassian.net' } })
  fireEvent.change(screen.getByLabelText('Integration credential name'), { target: { value: 'jira-delivery-token' } })
  fireEvent.change(screen.getByLabelText('Jira email'), { target: { value: 'owner@example.com' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save connection' }))
  const option = await screen.findByRole('option', { name: 'Delivery Jira · jira' })
  expect(option).toBeVisible()
  return option as HTMLOptionElement
}

it('prepares an exact Jira mutation for review and fails closed before network without its named credential', async () => {
  render(<IntegrationApps baseUrl={baseUrl} />)
  expect(screen.getByRole('region', { name: 'Integration applications' })).toBeVisible()
  expect(screen.getByText(/Credentials are named references/)).toBeVisible()
  expect(screen.getByRole('button', { name: 'Save connection' })).toBeDisabled()
  const option = await saveJira()
  fireEvent.change(screen.getByLabelText('Integration connection'), { target: { value: option.value } })
  expect(screen.getByLabelText('Integration operation')).toHaveValue('jira_auth')
  fireEvent.change(screen.getByLabelText('Integration operation'), { target: { value: 'jira_comment' } })
  fireEvent.change(screen.getByLabelText('Integration operation input'), { target: { value: JSON.stringify({ issue: 'OPS-4', comment: 'Owner reviewed release' }) } })
  fireEvent.click(screen.getByRole('button', { name: 'Prepare for review' }))
  const status = await screen.findByRole('status')
  expect(status).toHaveTextContent('prepared')
  expect(status).toHaveTextContent('POST /rest/api/3/issue/OPS-4/comment')
  expect(screen.getByText('Remote mutation: owner approval is required.')).toBeVisible()
  const article = status.closest('article')!
  expect(within(article).getByText(/Owner reviewed release/)).toBeVisible()
  expect(within(article).getByRole('button', { name: 'Approve and execute' })).not.toBeDisabled()
  fireEvent.click(within(article).getByRole('button', { name: 'Approve and execute' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('failed'))
  expect(screen.getByText('Named credential unavailable')).toBeVisible()
  expect(within(article).getByRole('button', { name: 'Approve and execute' })).toHaveAttribute('aria-disabled', 'true')
  const persisted = await (await fetch(`${baseUrl}/api/capabilities/platform/integration-apps`)).json()
  expect(persisted.connections).toHaveLength(1)
  expect(persisted.connections[0].credential_name).toBe('jira-delivery-token')
  expect(persisted.connections[0]).not.toHaveProperty('credential_value')
  expect(persisted.runs).toHaveLength(1)
  expect(persisted.runs[0].status).toBe('failed')
  expect(persisted.runs[0].result).toBeNull()
})

it('switches provider-specific connection fields and blocks malformed operation JSON locally', async () => {
  render(<IntegrationApps baseUrl={baseUrl} />)
  await screen.findByRole('option', { name: 'Delivery Jira · jira' })
  fireEvent.change(screen.getByLabelText('Integration provider'), { target: { value: 'datadog' } })
  expect(screen.getByLabelText('Integration API origin')).toHaveValue('https://api.datadoghq.com')
  expect(screen.getByLabelText('Integration operation')).toHaveValue('datadog_auth')
  expect(screen.getByLabelText('Datadog application credential name')).toBeVisible()
  expect(screen.queryByLabelText('Jira email')).toBeNull()
  fireEvent.change(screen.getByLabelText('Integration provider'), { target: { value: 'github' } })
  expect(screen.getByLabelText('Integration API origin')).toHaveValue('https://api.github.com')
  expect(screen.getByLabelText('Integration operation')).toHaveValue('github_auth')
  expect(screen.queryByLabelText('Datadog application credential name')).toBeNull()
  expect(screen.queryByLabelText('Jira email')).toBeNull()
  const option = screen.getByRole('option', { name: 'Delivery Jira · jira' }) as HTMLOptionElement
  fireEvent.change(screen.getByLabelText('Integration connection'), { target: { value: option.value } })
  fireEvent.change(screen.getByLabelText('Integration operation input'), { target: { value: '{bad-json' } })
  fireEvent.click(screen.getByRole('button', { name: 'Prepare for review' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('SyntaxError')
  const persisted = await (await fetch(`${baseUrl}/api/capabilities/platform/integration-apps`)).json()
  expect(persisted.connections).toHaveLength(1)
  expect(persisted.runs).toHaveLength(1)
})
