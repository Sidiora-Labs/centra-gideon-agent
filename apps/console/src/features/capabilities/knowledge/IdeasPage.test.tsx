import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import IdeasPage from './IdeasPage'

const originalFetch = globalThis.fetch
const home = mkdtempSync(resolve(tmpdir(), 'capture-console-'))
let child: ChildProcess
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/capabilities/knowledge/ideas_ui_server.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'],
  })
  let errors = ''
  child.stderr?.on('data', chunk => { errors += String(chunk) })
  const origin = await new Promise<string>((done, fail) => {
    let buffer = ''
    const timeout = setTimeout(() => fail(new Error(errors || 'Capture application startup timed out')), 15000)
    child.on('exit', code => { clearTimeout(timeout); fail(new Error(`Capture application exited ${code}: ${errors}`)) })
    child.stdout?.on('data', chunk => {
      buffer += String(chunk)
      for (const line of buffer.split('\n')) {
        if (!line.startsWith('{"port":')) continue
        try { const { port } = JSON.parse(line); clearTimeout(timeout); done(`http://127.0.0.1:${port}`) } catch { /* Wait for complete address. */ }
      }
    })
  })
  globalThis.fetch = (input, init) => originalFetch(typeof input === 'string' && input.startsWith('/') ? origin + input : input, init)
}, 20000)
afterEach(cleanup)
afterAll(async () => {
  globalThis.fetch = originalFetch
  if (child?.exitCode === null) {
    const stopped = new Promise<void>(done => child.once('exit', () => done()))
    child.kill('SIGTERM'); await stopped
  }
  rmSync(home, { recursive: true, force: true })
})

const markdown = `---
id: "d088c1bb-9c6a-4ccd-84c5-67aa3a13a113"
title: "Night observations"
category: "Astronomy"
status: "draft"
created: "2025-09-25T12:00:00Z"
modified: "2025-09-25T12:00:00Z"
tags: ["idea-loom"]
owner: "My notebook"
---
# What should we observe?

## Ideas
1. Observe Saturn
2. Photograph the Moon
`

it('reviews and imports actual Markdown, exports preserved fields and enables recurring vault sync', async () => {
  render(<IdeasPage />)
  await screen.findByRole('navigation', { name: 'Idea lists' })
  fireEvent.change(screen.getByLabelText('Idea-list Markdown'), { target: { value: markdown } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview import' }))
  await screen.findByRole('region', { name: 'Import review' })
  expect(screen.getByText('2 ordered ideas')).toBeInTheDocument()
  expect(screen.getByText('Preserved extra metadata: owner')).toBeInTheDocument()
  expect((await fetch('/api/capabilities/knowledge/ideas').then(r => r.json())).items).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: 'Import reviewed list' }))
  const firstIdea = await screen.findByRole('link', { name: 'Observe Saturn' })
  expect(firstIdea.getAttribute('href')).toMatch(/^#\/knowledge\/item\//)
  expect(screen.getByRole('link', { name: 'Open canonical collection' }).getAttribute('href')).toMatch(/^#\/knowledge\?collection=/)
  fireEvent.click(screen.getByRole('button', { name: 'Export Markdown' }))
  await screen.findByRole('region', { name: 'Exported Markdown' })
  const exported = screen.getByRole('textbox', { name: 'Exported Markdown' }) as HTMLTextAreaElement
  expect(exported.value).toContain('1. Observe Saturn')
  expect(exported.value).toContain('2. Photograph the Moon')
  expect(exported.value).toContain('owner: "My notebook"')
  expect(screen.getByRole('link', { name: 'Download Markdown' })).toHaveAttribute('download')
  fireEvent.click(screen.getByRole('button', { name: 'Sync owned vault' }))
  await screen.findByText('Sync result: unchanged')
  fireEvent.change(screen.getByLabelText('Sync interval minutes'), { target: { value: '15' } })
  fireEvent.click(screen.getByLabelText('Recurring sync enabled'))
  fireEvent.click(screen.getByRole('button', { name: 'Save sync schedule' }))
  await screen.findByText(/Next sync:/)
  const current = await fetch('/api/capabilities/knowledge/ideas').then(r => r.json())
  expect(current.items[0].sync_enabled).toBe(true)
  expect(current.items[0].minutes).toBe(15)
  fireEvent.click(screen.getByLabelText('Recurring sync enabled'))
  fireEvent.click(screen.getByRole('button', { name: 'Save sync schedule' }))
  await screen.findByText('Recurring sync disabled')
  expect((await fetch('/api/capabilities/knowledge/ideas').then(r => r.json())).items[0].sync_enabled).toBe(false)
})

it('rejects invalid numbered ideas without importing or discarding the draft', async () => {
  render(<IdeasPage />)
  const invalid = markdown.replace('1. Observe Saturn', '4. Observe Saturn')
  fireEvent.change(screen.getByLabelText('Idea-list Markdown'), { target: { value: invalid } })
  fireEvent.click(screen.getByRole('button', { name: 'Preview import' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('densely numbered')
  expect(screen.getByLabelText('Idea-list Markdown')).toHaveValue(invalid)
  expect(screen.queryByRole('button', { name: 'Import reviewed list' })).not.toBeInTheDocument()
})
