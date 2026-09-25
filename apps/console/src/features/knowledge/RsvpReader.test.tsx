import { afterAll, afterEach, beforeAll, beforeEach, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import type { KnowledgeItem } from '../../shared/data/api'
import { ReadingView } from './ReadingView'
import { RsvpReader, chunkAt, focalIndex, rsvpDelay, rsvpWords } from './RsvpReader'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'rsvp-console-'))
let child: ChildProcess
let origin = ''
let item: KnowledgeItem

beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/knowledge/rsvp_ui_server.py')], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let errors = ''
  child.stderr?.on('data', chunk => { errors += String(chunk) })
  const started = await new Promise<{ port: number; item: KnowledgeItem }>((done, fail) => {
    let buffer = ''
    const timeout = setTimeout(() => fail(new Error(errors || 'RSVP application startup timed out')), 15000)
    child.on('exit', code => { clearTimeout(timeout); fail(new Error(`RSVP application exited ${code}: ${errors}`)) })
    child.stdout?.on('data', chunk => {
      buffer += String(chunk)
      for (const line of buffer.split('\n')) {
        if (!line.startsWith('{"port":')) continue
        try { clearTimeout(timeout); done(JSON.parse(line)) } catch { /* wait */ }
      }
    })
  })
  origin = `http://127.0.0.1:${started.port}`
  item = started.item
  globalThis.fetch = (input, init) => originalFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, init)
}, 20000)

beforeEach(async () => {
  if (!item) return
  const current = await fetch(`/api/capabilities/knowledge/rsvp/${item.id}`).then(response => response.json())
  await fetch(`/api/capabilities/knowledge/rsvp/${item.id}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ word_index: 0, wpm: 350, chunk_size: 1, content_revision: current.content_revision }),
  })
})

afterEach(cleanup)
afterAll(async () => {
  globalThis.fetch = originalFetch
  if (child?.exitCode === null) {
    const stopped = new Promise<void>(done => child.once('exit', () => done()))
    child.kill('SIGTERM')
    await stopped
  }
  rmSync(home, { recursive: true, force: true })
})

it('preserves Unicode source offsets and attaches standalone punctuation', () => {
  const text = '“ Привет ” world … café, 你好! final'
  const words = rsvpWords(text)
  expect(words.map(word => word.text)).toEqual(['“Привет”', 'world…', 'café,', '你好!', 'final'])
  expect(text.slice(words[0].start, words[0].end).replaceAll(' ', '')).toBe('“Привет”')
  expect(text.slice(words[1].start, words[1].end).replaceAll(' ', '')).toBe('world…')
  expect(words.at(-1)?.end).toBe(text.length)
})

it('keeps canonical cursor stable while chunk presentation changes', () => {
  const words = rsvpWords('one two three four')
  expect(chunkAt(words, 2, 1)).toMatchObject({ text: 'three', count: 1 })
  expect(chunkAt(words, 2, 2)).toMatchObject({ text: 'three four', count: 2 })
  expect(chunkAt(rsvpWords('one exceptionallylongword next'), 1, 2)).toMatchObject({ text: 'exceptionallylongword', count: 1 })
  expect(focalIndex('“reader”')).toBe(3)
})

it('uses punctuation-aware timing and accounts for displayed chunk width', () => {
  expect(rsvpDelay('word', 600, 1)).toBe(100)
  expect(rsvpDelay('clause,', 600, 1)).toBe(130)
  expect(rsvpDelay('finished!', 600, 1)).toBe(180)
  expect(rsvpDelay('结束。', 600, 1)).toBe(180)
  expect(rsvpDelay('two words', 600, 2)).toBeCloseTo(230)
})

it('opens an actual stored article and changes chunk size without a cursor jump', async () => {
  render(<RsvpReader item={item} onClose={() => {}} />)
  const reader = await screen.findByRole('region', { name: 'Rapid reader' })
  expect(reader).toHaveTextContent('Grounded rapid article')
  expect(reader).toHaveTextContent('1 /')
  fireEvent.click(screen.getByRole('button', { name: 'Forward 5 words' }))
  await waitFor(() => expect(reader).toHaveTextContent('6 /'))
  fireEvent.click(screen.getByRole('button', { name: '2 words' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '2 words' })).toHaveAttribute('aria-pressed', 'true'))
  expect(reader).toHaveTextContent('6–7 /')
  const persisted = await waitFor(async () => {
    const value = await fetch(`/api/capabilities/knowledge/rsvp/${item.id}`).then(response => response.json())
    expect(value.word_index).toBe(5)
    expect(value.chunk_size).toBe(2)
    return value
  })
  expect(persisted.word_count).toBeGreaterThan(10)
})

it('pauses and resumes playback while persisting the new canonical position', async () => {
  render(<RsvpReader item={item} onClose={() => {}} />)
  const reader = await screen.findByRole('region', { name: 'Rapid reader' })
  fireEvent.change(screen.getByLabelText('Reading speed'), { target: { value: '1000' } })
  fireEvent.click(screen.getByRole('button', { name: 'Play' }))
  await waitFor(() => expect(reader).not.toHaveTextContent('1 /'), { timeout: 1500 })
  fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
  const paused = await waitFor(async () => {
    const value = await fetch(`/api/capabilities/knowledge/rsvp/${item.id}`).then(response => response.json())
    expect(value.wpm).toBe(1000)
    expect(value.word_index).toBeGreaterThan(0)
    return value
  })
  await new Promise(done => setTimeout(done, 180))
  const stillPaused = await fetch(`/api/capabilities/knowledge/rsvp/${item.id}`).then(response => response.json())
  expect(stillPaused.word_index).toBe(paused.word_index)
  expect(stillPaused.wpm).toBe(1000)
})

it('bookmarks and restores the exact word after moving elsewhere', async () => {
  render(<RsvpReader item={item} onClose={() => {}} />)
  const reader = await screen.findByRole('region', { name: 'Rapid reader' })
  fireEvent.click(screen.getByRole('button', { name: 'Forward 5 words' }))
  await waitFor(() => expect(reader).toHaveTextContent('6 /'))
  fireEvent.click(screen.getByRole('button', { name: 'Save RSVP bookmark' }))
  await waitFor(async () => {
    const persisted = await fetch(`/api/capabilities/knowledge/rsvp/${item.id}`).then(response => response.json())
    expect(persisted.bookmark_index).toBe(5)
    expect(screen.getByRole('button', { name: 'Restore bookmark' })).toBeEnabled()
  })
  fireEvent.click(screen.getByRole('button', { name: 'Forward 5 words' }))
  await waitFor(() => expect(reader).toHaveTextContent('11 /'))
  fireEvent.click(screen.getByRole('button', { name: 'Restore bookmark' }))
  await waitFor(() => expect(reader).toHaveTextContent('6 /'))
  await waitFor(async () => {
    const persisted = await fetch(`/api/capabilities/knowledge/rsvp/${item.id}`).then(response => response.json())
    expect(persisted.word_index).toBe(5)
    expect(persisted.bookmark_index).toBe(5)
  })
})

it('returns to the ordinary reader with its existing highlight intact', async () => {
  const quote = 'highlighted passage'
  render(<ReadingView item={item} annotations={[{
    id: 'annotation-1', item_id: item.id, quote, occurrence: 0, note: 'still here', created_at: '2026-09-25T00:00:00Z',
  }]} onAnnotationsChanged={() => {}} />)
  await waitFor(() => expect(document.querySelector('mark')).toHaveTextContent(quote))
  fireEvent.click(screen.getByRole('button', { name: 'Rapid read' }))
  expect(await screen.findByRole('region', { name: 'Rapid reader' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Return to article' }))
  expect(await screen.findByRole('group', { name: 'Article body' })).toBeInTheDocument()
  await waitFor(() => expect(document.querySelector('mark')).toHaveTextContent(quote))
  expect(screen.getByText((_text, node) => node?.tagName === 'SPAN' && !!node.textContent?.includes('1 highlight'))).toBeInTheDocument()
})

it('exposes API refusal without replacing it with fabricated reader state', async () => {
  render(<RsvpReader item={{ ...item, id: 'missing-item' }} onClose={() => {}} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('unavailable')
  expect(screen.queryByRole('region', { name: 'Rapid reader' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Return to article' })).toBeInTheDocument()
})
