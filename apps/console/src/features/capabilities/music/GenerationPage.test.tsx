import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import GenerationPage from './GenerationPage'
let server: ChildProcess
let apiBase: string
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/music/serve_generation.py'], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  const port = await new Promise<string>((done, reject) => {
    let output = '', errors = ''
    server.stderr!.on('data', chunk => { errors += String(chunk) })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`Generation HTTP service exited ${code}: ${errors}`)))
    server.stdout!.on('data', chunk => {
      output += String(chunk)
      const line = output.split('\n').find(value => /^\d+$/.test(value))
      if (line) done(line)
    })
  })
  apiBase = `http://127.0.0.1:${port}/api/capabilities/music/generation`
})
afterAll(() => { server?.kill('SIGTERM') })

it('persists a named engine configuration while keeping absent provider submission disabled', async () => {
  const page = render(<GenerationPage apiBase={apiBase} />)
  await screen.findByLabelText('Named credential')
  expect(screen.getByRole('button', { name: 'Compose music' })).toBeDisabled()
  expect(screen.getByText(/Remote availability and account entitlement remain unverified/)).toBeInTheDocument()
  expect(screen.getByLabelText('Enable music engine')).not.toBeChecked()
  fireEvent.click(screen.getByLabelText('Enable music engine'))
  fireEvent.change(screen.getByLabelText('Named credential'), { target: { value: 'missing-composer-key' } })
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'music_v2' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save engine settings' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save engine settings' })).not.toBeDisabled())
  const configResponse = await fetch(apiBase + '/config')
  const config = (await configResponse.json()).config
  expect(config.credential_name).toBe('missing-composer-key')
  expect(config.model).toBe('music_v2')
  expect(config.enabled).toBe(true)
  expect(config.revision).toBe(1)
  page.unmount()
  render(<GenerationPage apiBase={apiBase} />)
  await screen.findByLabelText('Named credential')
  expect(screen.getByLabelText('Named credential')).toHaveValue('missing-composer-key')
  expect(screen.getByLabelText('Model')).toHaveValue('music_v2')
  expect(screen.getByLabelText('Enable music engine')).toBeChecked()
  fireEvent.change(screen.getByLabelText('Track ID'), { target: { value: 'requested-track' } })
  fireEvent.change(screen.getByLabelText('Composition prompt'), { target: { value: 'Warm guitar instrumental' } })
  fireEvent.change(screen.getByLabelText('License or rights statement'), { target: { value: 'Review account terms' } })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh readiness' }))
  await screen.findByText('Engine disabled or named credential unavailable.')
  expect(screen.getByRole('button', { name: 'Compose music' })).toBeDisabled()
  const jobs = await fetch(apiBase + '/jobs')
  expect((await jobs.json()).jobs).toEqual([])
})

it('keeps unsaved credential references visible after a real optimistic conflict', async () => {
  render(<GenerationPage apiBase={apiBase} />)
  await screen.findByLabelText('Named credential')
  const response = await fetch(apiBase + '/config')
  const current = (await response.json()).config
  await fetch(apiBase + '/config', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...current, model: 'music_v2_5' }) })
  fireEvent.change(screen.getByLabelText('Named credential'), { target: { value: 'unsaved-reference' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save engine settings' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Engine configuration changed')
  expect(screen.getByLabelText('Named credential')).toHaveValue('unsaved-reference')
  expect(screen.getByRole('button', { name: 'Compose music' })).toBeDisabled()
  const unchanged = await fetch(apiBase + '/config')
  expect((await unchanged.json()).config.credential_name).toBe(current.credential_name)
})
