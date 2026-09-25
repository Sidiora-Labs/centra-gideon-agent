import { spawn, type ChildProcess } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { gzipSync } from 'node:zlib'
import { afterAll, beforeAll, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import Migration from './Migration'

let server: ChildProcess
let baseUrl: string
let home: string

function tarFile(files: Record<string, Buffer>) {
  const blocks: Buffer[] = []
  for (const [name, value] of Object.entries(files)) {
    const header = Buffer.alloc(512)
    header.write(name, 0, 100, 'utf8')
    header.write('0000644\0', 100, 8, 'ascii')
    header.write('0000000\0', 108, 8, 'ascii')
    header.write('0000000\0', 116, 8, 'ascii')
    header.write(`${value.length.toString(8).padStart(11, '0')}\0`, 124, 12, 'ascii')
    header.write(`${Math.floor(Date.now() / 1000).toString(8).padStart(11, '0')}\0`, 136, 12, 'ascii')
    header.fill(32, 148, 156)
    header.write('0', 156, 1, 'ascii')
    header.write('ustar\0', 257, 6, 'ascii')
    header.write('00', 263, 2, 'ascii')
    header.write(`${header.reduce((sum, byte) => sum + byte, 0).toString(8).padStart(6, '0')}\0 `, 148, 8, 'ascii')
    blocks.push(header, value, Buffer.alloc((512 - value.length % 512) % 512))
  }
  blocks.push(Buffer.alloc(1024))
  return gzipSync(Buffer.concat(blocks))
}

function fixture(extra: Record<string, Buffer> = {}) {
  const data: Record<string, Buffer> = {
    'brain/people/index.json': Buffer.from(JSON.stringify({ schemaVersion: 1, type: 'people', updatedAt: '2026-09-12T00:00:00Z', config: {} })),
    'brain/people/person-ui/index.json': Buffer.from(JSON.stringify({ id: 'person-ui', name: 'UI Archive Person', context: 'Imported through the real HTTP surface', createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-09-12T00:00:00Z' })),
    ...extra,
  }
  const manifest = { generatedAt: '2026-09-12T00:00:00.000Z', fileCount: Object.keys(data).length, files: Object.fromEntries(Object.entries(data).map(([name, value]) => [name, createHash('sha256').update(value).digest('hex')])) }
  return tarFile({ 'snapshot-ui/manifest.json': Buffer.from(JSON.stringify(manifest)), ...Object.fromEntries(Object.entries(data).map(([name, value]) => [`snapshot-ui/data/${name}`, value])) })
}

function knowledgeFixture() {
  const id = '88888888-8888-4888-8888-888888888888'
  const data: Record<string, Buffer> = {
    'brain/memories/index.json': Buffer.from(JSON.stringify({ schemaVersion: 1, type: 'memories', updatedAt: '2026-09-12T00:00:00Z', config: {} })),
    [`brain/memories/${id}/index.json`]: Buffer.from(JSON.stringify({ id, title: 'UI migrated memory', content: 'Canonical knowledge content', tags: ['migration'], source: 'archive', sourceRef: 'memory.json', createdAt: '2025-01-01T00:00:00Z', updatedAt: '2025-02-01T00:00:00Z' })),
  }
  const manifest = { generatedAt: '2026-09-12T00:00:00.000Z', fileCount: Object.keys(data).length, files: Object.fromEntries(Object.entries(data).map(([name, value]) => [name, createHash('sha256').update(value).digest('hex')])) }
  return tarFile({ 'snapshot-memory/manifest.json': Buffer.from(JSON.stringify(manifest)), ...Object.fromEntries(Object.entries(data).map(([name, value]) => [`snapshot-memory/data/${name}`, value])) })
}

function expandedKnowledgeFixture() {
  const idea = '13131313-1313-4313-8313-131313131313'
  const day = '2026-09-23'
  const data: Record<string, Buffer> = {
    'brain/ideas/index.json': Buffer.from(JSON.stringify({ schemaVersion: 1, type: 'ideas', updatedAt: '2026-09-23T00:00:00Z', config: {} })),
    [`brain/ideas/${idea}/index.json`]: Buffer.from(JSON.stringify({ id: idea, title: 'UI imported idea', status: 'active', oneLiner: 'A real migrated idea', notes: 'Preserved notes', tags: ['idea'], createdAt: '2025-01-01T00:00:00Z', updatedAt: '2025-02-01T00:00:00Z' })),
    'brain/journals/index.json': Buffer.from(JSON.stringify({ schemaVersion: 1, type: 'journals', updatedAt: '2026-09-23T00:00:00Z', config: {} })),
    [`brain/journals/${day}/index.json`]: Buffer.from(JSON.stringify({ id: day, date: day, content: 'UI imported journal', segments: [{ text: 'UI imported journal', at: '2026-09-23T10:00:00Z', source: 'text' }], createdAt: '2026-09-23T10:00:00Z', updatedAt: '2026-09-23T10:00:00Z' })),
  }
  const manifest = { generatedAt: '2026-09-23T10:00:00.000Z', fileCount: Object.keys(data).length, files: Object.fromEntries(Object.entries(data).map(([name, value]) => [name, createHash('sha256').update(value).digest('hex')])) }
  return tarFile({ 'snapshot-expanded/manifest.json': Buffer.from(JSON.stringify(manifest)), ...Object.fromEntries(Object.entries(data).map(([name, value]) => [`snapshot-expanded/data/${name}`, value])) })
}

function adminFixture() {
  const id = '35353535-3535-4535-8535-353535353535'
  const data: Record<string, Buffer> = {
    'brain/admin/index.json': Buffer.from(JSON.stringify({ schemaVersion: 1, type: 'admin', updatedAt: '2026-09-24T00:00:00Z', config: {} })),
    [`brain/admin/${id}/index.json`]: Buffer.from(JSON.stringify({ id, title: 'UI imported action', status: 'open', nextAction: 'Complete the form', notes: 'Canonical task notes', createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-09-24T00:00:00Z' })),
  }
  const manifest = { generatedAt: '2026-09-24T00:00:00.000Z', fileCount: Object.keys(data).length, files: Object.fromEntries(Object.entries(data).map(([name, value]) => [name, createHash('sha256').update(value).digest('hex')])) }
  return tarFile({ 'snapshot-admin/manifest.json': Buffer.from(JSON.stringify(manifest)), ...Object.fromEntries(Object.entries(data).map(([name, value]) => [`snapshot-admin/data/${name}`, value])) })
}

function songFixture() {
  const id = 'song-ui'
  const score = Buffer.from('%PDF-1.7\nUI score')
  const data: Record<string, Buffer> = {
    'brain/songs/index.json': Buffer.from(JSON.stringify({ schemaVersion: 1, type: 'songs', updatedAt: '2026-09-25T00:00:00Z', config: {} })),
    [`brain/songs/${id}/index.json`]: Buffer.from(JSON.stringify({ id, title: 'UI imported song', artist: 'UI Band', instrument: 'guitar', stage: 'learning', tags: ['live'], key: 'C', capo: 1, tuning: 'standard', sourceUrl: 'https://example.test/song', links: [], content: { format: 'chordpro', text: '[C]Hello' }, notes: 'Real repertoire import', scrollDurationSec: 60, attachments: [{ filename: 'ui-score.pdf', label: 'UI score', mime: 'application/pdf', size: score.length, sha256: createHash('sha256').update(score).digest('hex') }], createdAt: '2026-01-01T00:00:00+00:00', updatedAt: '2026-09-25T00:00:00+00:00' })),
    'brain/songbook/ui-score.pdf': score,
  }
  const manifest = { generatedAt: '2026-09-25T00:00:00.000Z', fileCount: Object.keys(data).length, files: Object.fromEntries(Object.entries(data).map(([name, value]) => [name, createHash('sha256').update(value).digest('hex')])) }
  return tarFile({ 'snapshot-song/manifest.json': Buffer.from(JSON.stringify(manifest)), ...Object.fromEntries(Object.entries(data).map(([name, value]) => [`snapshot-song/data/${name}`, value])) })
}

beforeAll(async () => {
  home = await mkdtemp(`${tmpdir()}/gideon-migration-`)
  const root = resolve(process.cwd(), '../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['checks/runtime/capabilities/platform/migration_ui_server.py'], { cwd: root, env: { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home, GIDEON_DEV_NO_AUTH: '1' }, stdio: ['ignore', 'pipe', 'pipe'] })
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

it('previews checksummed people then commits through the actual API and reloads its receipt', async () => {
  const { unmount } = render(<Migration baseUrl={baseUrl} />)
  await screen.findByText('No archive imports recorded.')
  const file = new File([fixture()], 'snapshot.tar.gz', { type: 'application/gzip' })
  fireEvent.change(screen.getByLabelText('Snapshot archive'), { target: { files: [file] } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview verified archive' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview verified archive' }))
  expect(await screen.findByText(/1 records verified/)).toBeVisible()
  expect(screen.getByText('people: UI Archive Person')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Import reviewed records' }))
  expect(await screen.findByText(/1 people ·/)).toBeVisible()
  const state = await (await fetch(`${baseUrl}/api/capabilities/platform/migration`)).json()
  expect(state.receipts).toHaveLength(1)
  expect(state.receipts[0].domains).toEqual({ people: 1 })
  unmount()
  render(<Migration baseUrl={baseUrl} />)
  expect(await screen.findByText(/1 people ·/)).toBeVisible()
})

it('refuses an archive containing an unsupported domain without showing an import action', async () => {
  render(<Migration baseUrl={baseUrl} />)
  const file = new File([fixture({ 'media/unmapped.bin': Buffer.from('unmapped') })], 'unsupported.tar.gz', { type: 'application/gzip' })
  fireEvent.change(screen.getByLabelText('Snapshot archive'), { target: { files: [file] } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview verified archive' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview verified archive' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('unsupported domains: media')
  expect(screen.queryByRole('button', { name: 'Import reviewed records' })).not.toBeInTheDocument()
})

it('imports a checksummed memory into canonical knowledge and reports its durable receipt', async () => {
  render(<Migration baseUrl={baseUrl} />)
  const file = new File([knowledgeFixture()], 'memory.tar.gz', { type: 'application/gzip' })
  fireEvent.change(screen.getByLabelText('Snapshot archive'), { target: { files: [file] } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview verified archive' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview verified archive' }))
  expect(await screen.findByText('Domains: memories')).toBeVisible()
  expect(screen.getByText('memories: UI migrated memory')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Import reviewed records' }))
  expect(await screen.findByText(/1 memories ·/)).toBeVisible()
  const state = await (await fetch(`${baseUrl}/api/capabilities/platform/migration`)).json()
  expect(state.supported_domains).toEqual(['people', 'projects', 'ideas', 'journals', 'memories', 'links', 'buckets', 'inbox', 'admin', 'threads', 'songs'])
  expect(state.receipts.some((row: { domains: Record<string, number> }) => row.domains.memories === 1)).toBe(true)
})

it('reviews and atomically imports mixed ideas and journals through the actual API', async () => {
  render(<Migration baseUrl={baseUrl} />)
  const file = new File([expandedKnowledgeFixture()], 'expanded.tar.gz', { type: 'application/gzip' })
  fireEvent.change(screen.getByLabelText('Snapshot archive'), { target: { files: [file] } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview verified archive' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview verified archive' }))
  expect(await screen.findByText('Domains: ideas, journals')).toBeVisible()
  expect(screen.getByText('ideas: UI imported idea')).toBeVisible()
  expect(screen.getByText('journals: Journal for 2026-09-23')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Import reviewed records' }))
  expect(await screen.findByText(/1 ideas, 1 journals ·/)).toBeVisible()
})

it('imports one admin action through the API into the canonical task surface', async () => {
  render(<Migration baseUrl={baseUrl} />)
  const file = new File([adminFixture()], 'admin.tar.gz', { type: 'application/gzip' })
  fireEvent.change(screen.getByLabelText('Snapshot archive'), { target: { files: [file] } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview verified archive' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview verified archive' }))
  expect(await screen.findByText('Domains: admin')).toBeVisible()
  expect(screen.getByText('admin: UI imported action')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Import reviewed records' }))
  expect(await screen.findByText(/1 admin ·/)).toBeVisible()
  const state = await (await fetch(`${baseUrl}/api/capabilities/platform/migration`)).json()
  expect(state.receipts.some((row: { domains: Record<string, number> }) => row.domains.admin === 1)).toBe(true)
})

it('imports a verified song and attachment through the API into the canonical repertoire', async () => {
  render(<Migration baseUrl={baseUrl} />)
  const file = new File([songFixture()], 'song.tar.gz', { type: 'application/gzip' })
  fireEvent.change(screen.getByLabelText('Snapshot archive'), { target: { files: [file] } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview verified archive' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Preview verified archive' }))
  expect(await screen.findByText('Domains: songs')).toBeVisible()
  expect(screen.getByText('songs: UI imported song')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Import reviewed records' }))
  expect(await screen.findByText(/1 songs ·/)).toBeVisible()
  const state = await (await fetch(`${baseUrl}/api/capabilities/platform/migration`)).json()
  expect(state.receipts.some((row: { domains: Record<string, number> }) => row.domains.songs === 1)).toBe(true)
  const repertoire = await (await fetch(`${baseUrl}/api/capabilities/music/items/song-ui`)).json()
  expect(repertoire.item.artist).toBe('UI Band')
  expect(repertoire.item.attachment_availability[0]).toMatchObject({ available: true, kind: 'pdf', mime: 'application/pdf' })
})
