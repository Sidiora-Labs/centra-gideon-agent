import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import Authors from './Authors'

let server: ChildProcess
let apiRoot: string
let home: string
const repository = resolve(process.cwd(), '../..')

const nativeFetch = globalThis.fetch
beforeAll(async () => {
  home = await Promise.resolve(mkdtempSync(`${tmpdir()}/gideon-authors-ui-`))
  const root = repository
  const childEnv: NodeJS.ProcessEnv = { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home }
  delete childEnv.GIDEON_DEV_NO_AUTH
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', [resolve(root, 'checks/runtime/capabilities/creative/author_server.py'), home], { cwd: root, env: childEnv, stdio: ['ignore', 'pipe', 'pipe'] })
  let diagnostics = ''
  server.stderr!.on('data', chunk => { diagnostics += chunk.toString() })
  const ready = await new Promise<{ url: string; token: string; work_id: string; revision: number }>((accept, reject) => {
    const lines = createInterface({ input: server.stdout! })
    lines.on('line', line => {
      try {
        const value = JSON.parse(line)
        if (typeof value.url !== 'string' || typeof value.token !== 'string') return
        accept(value); lines.close()
      } catch { /* Read the native child readiness record. */ }
    })
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP process exited ${code}: ${diagnostics}`)))
  })
  const baseUrl = ready.url
  apiRoot = `${baseUrl}/api/capabilities/creative/authors`
  expect((await nativeFetch(`${baseUrl}/api/capabilities/creative/authors`)).status).toBe(403)
  globalThis.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), baseUrl)
    if (url.origin !== baseUrl) return nativeFetch(input, init)
    const headers = new Headers(init?.headers)
    headers.set('Authorization', `Bearer ${ready.token}`)
    return nativeFetch(url, { ...init, headers })
  }
})
afterEach(() => { cleanup(); location.hash = '' })
afterAll(async () => {
  globalThis.fetch = nativeFetch
  if (server && server.exitCode === null) {
    const ended = new Promise<void>(done => server.once('exit', () => done()))
    server.kill('SIGTERM'); await ended
  }
  if (home) rmSync(home, { recursive: true, force: true })
})

function change(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}
async function current() {
  const id = new URLSearchParams(location.hash.split('?')[1]).get('author')!
  const response = await fetch(`${apiRoot}/${id}`)
  expect(response.status).toBe(200)
  return response.json()
}
async function ready() {
  await waitFor(() => expect(screen.queryByText('Loading authors…')).not.toBeInTheDocument())
}

describe('Literary author and configured voice journeys', () => {
  it('persists authored voice and pinned sample, builds actual brief and restores history', async () => {
    render(<Authors apiRoot={apiRoot} />)
    await screen.findByText('No authors found.')
    change('Author name', 'Mira')
    change('Author biography', 'Writes about cities.')
    change('Perspective', 'third')
    change('Tense', 'past')
    change('Tone', 'Reflective')
    change('Diction', 'Concrete')
    change('Rhythm', 'Varied')
    change('Avoid in writing', 'Cliches')
    await screen.findByRole('option', { name: 'Literary sample · version 1' })
    change('Pin writing sample', 'literary-sample')
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Save author' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Save author' }))
    await screen.findByText('Author revision 1')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save author' })).toBeEnabled())
    const first = await current()
    expect(first.voice).toEqual({ perspective: 'third', tense: 'past', tone: 'Reflective', diction: 'Concrete', rhythm: 'Varied', avoid: 'Cliches' })
    expect(first.sample_refs).toEqual([{ artifact_id: 'literary-sample', artifact_version: 1 }])
    expect(first.sample_status[0].missing).toBe(false)
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Build voice brief' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Build voice brief' }))
    const brief = await screen.findByRole('region', { name: 'Configured voice brief' })
    expect(brief).toHaveTextContent('The night train crossed the city.')
    expect(brief).toHaveTextContent('tone: Reflective')
    expect(brief).toHaveTextContent('Writes about cities.')
    change('Tone', 'Urgent')
    change('Perspective', 'first')
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Save author' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Save author' }))
    await screen.findByText('Author revision 2')
    expect(screen.queryByRole('region', { name: 'Configured voice brief' })).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save author' })).toBeEnabled())
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Restore author revision 1' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Restore author revision 1' }))
    await screen.findByText('Author revision 3')
    expect(screen.getByLabelText('Tone')).toHaveValue('Reflective')
    expect(screen.getByLabelText('Perspective')).toHaveValue('third')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Export author' })).toBeEnabled())
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Export author' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Export author' }))
    const output = await screen.findByLabelText('Author export') as HTMLTextAreaElement
    const exported = JSON.parse(output.value)
    expect(exported.revision).toBe(3)
    expect(exported.voice).toEqual(first.voice)
    expect(exported.sample_refs).toEqual(first.sample_refs)
    cleanup()
    render(<Authors apiRoot={apiRoot} />)
    await screen.findByText('Author revision 3')
    expect(screen.getByLabelText('Author biography')).toHaveValue('Writes about cities.')
    expect(screen.getByLabelText('Avoid in writing')).toHaveValue('Cliches')
  })

  it('shows bounded actual sample excerpts and searches sample sources', async () => {
    render(<Authors apiRoot={apiRoot} />)
    await ready()
    change('Author name', 'Long samples author')
    change('Search writing samples', 'Long')
    await waitFor(() => expect(screen.queryByRole('option', { name: 'Literary sample · version 1' })).not.toBeInTheDocument())
    await screen.findByRole('option', { name: 'Long sample · version 1' })
    change('Pin writing sample', 'long-sample')
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Save author' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Save author' }))
    await screen.findByText('Author revision 1')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Build voice brief' })).toBeEnabled())
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Build voice brief' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Build voice brief' }))
    const brief = await screen.findByRole('region', { name: 'Configured voice brief' })
    expect(brief).toHaveTextContent('5000 source characters · Excerpt limited to 4000 characters')
    expect(brief.querySelector('pre')?.textContent).toHaveLength(4000)
    expect(brief.querySelector('pre')?.textContent).toBe('A'.repeat(4000))
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Unpin sample long-sample' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Unpin sample long-sample' }))
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Save author' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Save author' }))
    await screen.findByText('Author revision 2')
    expect((await current()).sample_refs).toEqual([])
  })

  it('rejects stale writes and resets missing deep links', async () => {
    render(<Authors apiRoot={apiRoot} />)
    await ready()
    change('Author name', 'Conflict author')
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Save author' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Save author' }))
    await screen.findByText('Author revision 1')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save author' })).toBeEnabled())
    const first = await current()
    const changed = await fetch(`${apiRoot}/${first.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: 1, title: 'External change' }) })
    expect(changed.status).toBe(200)
    change('Author name', 'Stale name')
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Save author' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Save author' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Author changed')
    expect((await current()).title).toBe('External change')
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Retry' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await screen.findByText('Author revision 2')
    expect(screen.getByLabelText('Author name')).toHaveValue('External change')
    location.hash = '#/capabilities/creative?view=authors&author=missing'
    fireEvent(window, new HashChangeEvent('hashchange'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Author not found')
    expect(screen.getByLabelText('Author name')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Save author' })).toBeDisabled()
    location.hash = '#/capabilities/creative?view=authors'
    fireEvent(window, new HashChangeEvent('hashchange'))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save author' })).toBeEnabled())
    expect(screen.queryByText('Author revision 2')).not.toBeInTheDocument()
  })

  it('searches and paginates persisted author profiles', async () => {
    for (let index = 0; index < 26; index++) {
      const response = await fetch(apiRoot, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: `Paged writer ${index}`, request_id: crypto.randomUUID() }) })
      expect(response.status).toBe(201)
    }
    render(<Authors apiRoot={apiRoot} />)
    await ready()
    change('Search authors', 'Paged writer')
    await screen.findByText('26 authors')
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Next page' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled())
    await ready()
    expect(screen.getByRole('button', { name: 'Next page' })).toHaveAttribute('aria-disabled', 'true')
    await waitFor(() => { const button = screen.getByRole('button', { name: 'Previous page' }); expect(button).not.toBeDisabled(); expect(button).not.toHaveAttribute('aria-disabled', 'true') }); fireEvent.click(screen.getByRole('button', { name: 'Previous page' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous page' })).toHaveAttribute('aria-disabled', 'true'))
    change('Search authors', 'Unmatched author')
    await screen.findByText('No authors found.')
    expect(screen.getByText('0 authors')).toBeInTheDocument()
  })
})
