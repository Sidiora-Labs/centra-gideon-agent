import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import VideosPage from './VideosPage'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'videos-console-'))
let child: ChildProcess
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/knowledge/videos_ui_server.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let errors = ''
  child.stderr?.on('data', chunk => { errors += String(chunk) })
  const origin = await new Promise<string>((done, fail) => {
    let buffer = ''
    const timeout = setTimeout(() => fail(new Error(errors || 'Video application startup timed out')), 15000)
    child.on('exit', code => { clearTimeout(timeout); fail(new Error(`Video application exited ${code}: ${errors}`)) })
    child.stdout?.on('data', chunk => {
      buffer += String(chunk)
      for (const line of buffer.split('\n')) {
        if (!line.startsWith('{"port":')) continue
        try { const { port } = JSON.parse(line); clearTimeout(timeout); done(`http://127.0.0.1:${port}`) } catch { /* wait for complete output */ }
      }
    })
  })
  globalThis.fetch = (input, init) => originalFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, init)
}, 20000)
afterEach(cleanup)
afterAll(async () => {
  globalThis.fetch = originalFetch
  if (child?.exitCode === null) { const stopped = new Promise<void>(done => child.once('exit', () => done())); child.kill('SIGTERM'); await stopped }
  rmSync(home, { recursive: true, force: true })
})

const url = 'https://youtu.be/dQw4w9WgXcQ'
const captions = `WEBVTT

00:00:01.000 --> 00:00:03.000
First grounded caption

00:01:02.000 --> 00:01:05.000
Second grounded caption
`

it('reviews supplied captions, persists them through the real API and opens the canonical transcript', async () => {
  render(<VideosPage />)
  await screen.findByRole('navigation', { name: 'Video ingests' })
  fireEvent.change(screen.getByLabelText('Video URL'), { target: { value: url } })
  fireEvent.change(screen.getByLabelText('Video title'), { target: { value: 'Knowledge from a talk' } })
  fireEvent.change(screen.getByLabelText('Caption content'), { target: { value: captions } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview captions' }))
  const review = await screen.findByRole('region', { name: 'Caption review' })
  expect(review).toHaveTextContent('2 timed segments · en')
  expect(screen.getByRole('link', { name: 'Open first timestamp' })).toHaveAttribute('href', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1s')
  expect(within(review).getByText(/First grounded caption/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Save reviewed transcript' }))
  const selected = await screen.findByRole('region', { name: 'Selected video ingest' })
  expect(selected).toHaveTextContent('Status: completed · complete')
  expect(screen.getByRole('link', { name: 'Open source record' }).getAttribute('href')).toMatch(/^#\/knowledge\/item\//)
  fireEvent.click(screen.getByRole('button', { name: 'Read transcript' }))
  expect(await screen.findByRole('region', { name: 'Stored transcript' })).toHaveTextContent('Second grounded caption')
  const saved = await fetch('/api/capabilities/knowledge/videos').then(response => response.json())
  expect(saved.total).toBe(1)
  expect(saved.items[0].events.map((event: { status: string }) => event.status)).toEqual(['pending', 'running', 'completed'])
})

it('keeps the draft visible when caption review rejects malformed timing', async () => {
  render(<VideosPage />)
  await screen.findByRole('navigation', { name: 'Video ingests' })
  const broken = 'WEBVTT\n\n00:broken --> 00:01.000\nKeep this draft'
  fireEvent.change(screen.getByLabelText('Video URL'), { target: { value: url } })
  fireEvent.change(screen.getByLabelText('Video title'), { target: { value: 'Broken transcript' } })
  fireEvent.change(screen.getByLabelText('Caption content'), { target: { value: broken } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview captions' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('timestamp')
  expect(screen.getByLabelText('Caption content')).toHaveValue(broken)
  expect(screen.queryByRole('region', { name: 'Caption review' })).not.toBeInTheDocument()
})

it('creates an honest durable acquisition job and exposes its terminal adapter outcome', async () => {
  render(<VideosPage />)
  await screen.findByRole('navigation', { name: 'Video ingests' })
  fireEvent.change(screen.getByLabelText('Video URL'), { target: { value: url } })
  fireEvent.click(screen.getByRole('button', { name: 'Start acquisition' }))
  const selected = await screen.findByRole('region', { name: 'Selected video ingest' })
  expect(selected).toHaveTextContent(/Status: (pending|running|failed|completed)/)
  fireEvent.click(screen.getByRole('button', { name: 'Refresh status' }))
  await waitFor(() => expect(screen.getByRole('region', { name: 'Selected video ingest' })).toHaveTextContent(/Status: (failed|completed)/))
  const jobs = await fetch('/api/capabilities/knowledge/videos').then(response => response.json())
  const acquired = jobs.items.find((item: { request_id: string }) => item.request_id !== undefined && item.title === 'Public video') || jobs.items[0]
  expect(acquired.events.length).toBeGreaterThanOrEqual(2)
})

it('requires an artifact selection and does not submit when all choices are off', async () => {
  render(<VideosPage />)
  await screen.findByRole('navigation', { name: 'Video ingests' })
  fireEvent.change(screen.getByLabelText('Video URL'), { target: { value: url } })
  fireEvent.click(screen.getByLabelText('Retrieve captions'))
  const start = screen.getByRole('button', { name: 'Start acquisition' })
  expect(start).toBeDisabled()
  fireEvent.click(screen.getByLabelText('Download audio'))
  expect(start).toBeEnabled()
  fireEvent.click(screen.getByLabelText('Download video'))
  expect(screen.getByLabelText('Download audio')).toBeChecked()
  expect(screen.getByLabelText('Download video')).toBeChecked()
})
