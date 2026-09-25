import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import CatalogPage from './CatalogPage'

let server: ChildProcess
let apiBase: string
let audioSlug: string
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/music/serve_catalog.py'], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime') }, stdio: ['ignore', 'pipe', 'pipe'] })
  const info = await new Promise<{ port: number; slug: string }>((done, reject) => {
    let output = '', errors = ''
    server.stderr!.on('data', chunk => { errors += String(chunk) })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`Catalog HTTP service exited ${code}: ${errors}`)))
    server.stdout!.on('data', chunk => {
      output += String(chunk)
      const row = output.split('\n').find(line => line.startsWith('{"port":'))
      if (row) done(JSON.parse(row))
    })
  })
  apiBase = `http://127.0.0.1:${info.port}/api/capabilities/music/catalog`
  audioSlug = info.slug
})
afterAll(() => { server?.kill('SIGTERM') })

it('creates metadata, attaches canonical measured audio, and preserves selected playback after reload', async () => {
  window.location.hash = '#/capabilities/music/catalog/tracks'
  const page = render(<CatalogPage apiBase={apiBase} />)
  await screen.findByText('No matching records.')
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Recorded rehearsal' } })
  fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Unprocessed room recording' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create record' }))
  await screen.findByRole('article', { name: 'Audio renders' })
  fireEvent.change(screen.getByLabelText('Audio artifact slug'), { target: { value: audioSlug } })
  fireEvent.change(screen.getByLabelText('Source attribution'), { target: { value: 'Our rehearsal' } })
  fireEvent.change(screen.getByLabelText('License or rights statement'), { target: { value: 'Owned recording' } })
  fireEvent.click(screen.getByRole('button', { name: 'Attach recording' }))
  await screen.findByText(/1.000 seconds · imported/)
  expect(screen.getByText('Our rehearsal · Owned recording (user supplied)')).toBeInTheDocument()
  expect(screen.getByLabelText(`Play ${audioSlug}`)).toHaveAttribute('src', `/api/artifacts/${audioSlug}/raw?version=1`)
  expect(screen.getByRole('button', { name: 'Selected render' })).toBeDisabled()
  const hash = window.location.hash
  page.unmount()
  render(<CatalogPage apiBase={apiBase} />)
  await screen.findByText(/1.000 seconds · imported/)
  expect(screen.getByLabelText('Notes')).toHaveValue('Unprocessed room recording')
  expect(window.location.hash).toBe(hash)
  expect(screen.getByText(/Generation provenance is unavailable/)).toBeInTheDocument()
})

it('authors artists and an ordered album and archives through real HTTP', async () => {
  window.location.hash = '#/capabilities/music/catalog/artists'
  render(<CatalogPage apiBase={apiBase} />)
  await screen.findByRole('button', { name: 'Create record' })
  fireEvent.change(screen.getByLabelText('Artist name'), { target: { value: 'Local quartet' } })
  fireEvent.change(screen.getByLabelText('Biography'), { target: { value: 'Four acoustic instruments' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create record' }))
  await screen.findByRole('button', { name: 'Archive record' })
  const artistId = window.location.hash.split('/').at(-1)
  const trackResponse = await fetch(apiBase + '/tracks')
  const trackId = (await trackResponse.json()).items[0].id
  fireEvent.click(screen.getByRole('button', { name: 'albums' }))
  await screen.findByRole('button', { name: 'Create record' })
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Studio collection' } })
  fireEvent.change(screen.getByLabelText('Artist ID (optional)'), { target: { value: artistId } })
  fireEvent.change(screen.getByLabelText('Ordered track IDs (one per line)'), { target: { value: trackId } })
  fireEvent.click(screen.getByRole('button', { name: 'Create record' }))
  await screen.findByRole('button', { name: 'Archive record' })
  expect(screen.getByLabelText('Artist ID (optional)')).toHaveValue(artistId)
  expect(screen.getByLabelText('Ordered track IDs (one per line)')).toHaveValue(trackId)
  fireEvent.click(screen.getByRole('button', { name: 'Archive record' }))
  await screen.findByRole('button', { name: 'Restore record' })
  fireEvent.click(screen.getByRole('button', { name: 'albums' }))
  await screen.findByText('No matching records.')
  fireEvent.click(screen.getByLabelText('Archived records'))
  await screen.findByRole('link', { name: 'Studio collection' })
  fireEvent.change(screen.getByLabelText('Filter catalog'), { target: { value: 'no match' } })
  await waitFor(() => expect(screen.queryByRole('link', { name: 'Studio collection' })).not.toBeInTheDocument())
})
