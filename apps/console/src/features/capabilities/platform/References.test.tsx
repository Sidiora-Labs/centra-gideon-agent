import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtemp, rm, rename } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { beforeAll, afterAll, expect, it } from 'vitest'
import { fireEvent, render, screen, within, waitFor } from '@testing-library/react'
import References from './References'
let server: ChildProcess
let baseUrl: string
let home: string
beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-references-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/references_ui_server.py'], {
    cwd: root, env: { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home, GIDEON_DEV_NO_AUTH: '1' }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let diagnostics = ''
  server.stderr!.on('data', chunk => { diagnostics += chunk.toString() })
  baseUrl = await new Promise<string>((accept, reject) => {
    const lines = createInterface({ input: server.stdout! })
    lines.on('line', line => { if (/^\d+$/.test(line)) { accept(`http://127.0.0.1:${line}`); lines.close() } })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP process exited ${code}: ${diagnostics}`)))
  })
})
afterAll(async () => {
  if (server && server.exitCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM') })
  await rm(home, { recursive: true, force: true })
})
function fill(path = 'reference') {
  fireEvent.change(screen.getByLabelText('Reference name'), { target: { value: 'Docs' } })
  fireEvent.change(screen.getByLabelText('Reference path'), { target: { value: path } })
}
it('tracks actual checkout, checks commits without reviewing, retains stale snapshot and reviews explicitly', async () => {
  const rendered = render(<References baseUrl={baseUrl} />)
  expect(await screen.findByText('No reference repositories.')).toBeVisible()
  fill()
  fireEvent.click(screen.getByRole('button', { name: 'Track reference' }))
  let article = within(await screen.findByRole('article', { name: 'Docs' }))
  expect(article.getByText('Reviewed: Never')).toBeVisible()
  expect(article.getByRole('button', { name: 'Mark Docs reviewed' })).toBeDisabled()
  fireEvent.click(article.getByRole('button', { name: 'Check Docs' }))
  expect(await article.findByText(/actual reference revision 2/)).toBeVisible()
  expect(article.getByText(/actual reference revision 1/)).toBeVisible()
  expect(article.getByText('Reviewed: Never')).toBeVisible()
  await rename(`${home}/upstream`, `${home}/offline-upstream`)
  fireEvent.click(article.getByRole('button', { name: 'Check Docs' }))
  expect(await article.findByText(/Stale snapshot/)).toBeVisible()
  expect(article.getByText(/actual reference revision 2/)).toBeVisible()
  expect(article.getByRole('button', { name: 'Mark Docs reviewed' })).toBeDisabled()
  await rename(`${home}/offline-upstream`, `${home}/upstream`)
  fireEvent.click(article.getByRole('button', { name: 'Check Docs' }))
  await waitFor(() => expect(article.queryByText(/Stale snapshot/)).toBeNull())
  fireEvent.click(article.getByRole('button', { name: 'Mark Docs reviewed' }))
  expect(await article.findByText('No unreviewed commits.')).toBeVisible()
  const data = await (await fetch(`${baseUrl}/api/capabilities/platform/references`)).json()
  expect(data.references[0].reviewed).toBe(data.references[0].snapshot.head)
  rendered.unmount()
  render(<References baseUrl={baseUrl} />)
  article = within(await screen.findByRole('article', { name: 'Docs' }))
  expect(article.getByText('No unreviewed commits.')).toBeVisible()
  fireEvent.click(article.getByRole('button', { name: 'Remove Docs' }))
  expect(await screen.findByText('No reference repositories.')).toBeVisible()
})
it('preserves rejected path and displays actual containment failure', async () => {
  render(<References baseUrl={baseUrl} />)
  await screen.findByText('No reference repositories.')
  fill('../upstream')
  fireEvent.click(screen.getByRole('button', { name: 'Track reference' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('inside the workspace')
  expect(screen.getByLabelText('Reference path')).toHaveValue('../upstream')
  expect(screen.queryByRole('article', { name: 'Docs' })).toBeNull()
})
