import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { AppsSection } from './AppsSection'
import ts from 'typescript'

const originalFetch = globalThis.fetch
const home = mkdtempSync(join(tmpdir(), 'apps-observability-'))
let child: ChildProcess
let source = ''
let collectionReads = 0
beforeAll(async () => {
  const root = resolve(process.cwd(), '../..')
  child = spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', [resolve(root, 'checks/runtime/apps_observability_ui_server.py')], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'runtime'), GIDEON_HOME: home }, stdio: ['ignore', 'pipe', 'pipe'] })
  let errors = '', token = ''
  child.stderr?.on('data', chunk => { errors += String(chunk) })
  const origin = await new Promise<string>((done, fail) => {
    let buffer = ''; const timeout = setTimeout(() => fail(new Error(errors || 'App server startup timed out')), 20000)
    child.on('exit', code => { clearTimeout(timeout); fail(new Error(`App server exited ${code}: ${errors}`)) })
    child.stdout?.on('data', chunk => { buffer += String(chunk); for (const line of buffer.split('\n')) {
      if (!line.startsWith('{"port":')) continue
      const ready = JSON.parse(line)
      if (typeof ready.token !== 'string' || !ready.token) { fail(new Error('Missing owner token')); return }
      token = ready.token; source = ready.source; clearTimeout(timeout); done(`http://127.0.0.1:${ready.port}`)
    } })
  })
  expect((await originalFetch(origin + '/api/apps')).status).toBe(403)
  globalThis.fetch = (input, init) => {
    const target = typeof input === 'string' && input.startsWith('/') ? origin + input : input
    const url = target instanceof Request ? target.url : String(target)
    const headers = new Headers(target instanceof Request ? target.headers : undefined)
    new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
    if (new URL(url).origin === origin) {
      headers.set('Authorization', `Bearer ${token}`)
      if (new URL(url).pathname === '/api/apps' && (!init?.method || init.method === 'GET')) collectionReads++
    }
    return originalFetch(target, { ...init, headers })
  }
}, 25000)
afterEach(cleanup)
afterAll(async () => {
  globalThis.fetch = originalFetch
  if (child?.exitCode === null) { const stopped = new Promise<void>(done => child.once('exit', () => done())); child.kill('SIGTERM'); await stopped }
  rmSync(home, { recursive: true, force: true })
})

describe('installed app observability', () => {
  it('re-reads the installed collection after native reviewed update and repaints the version', async () => {
    render(<AppsSection query={{ view: 'library' }} setQuery={() => {}} navigate={() => {}} />)
    await screen.findByText('v1.0.0')
    const beforeUpdate = collectionReads
    await userEvent.click(screen.getByRole('button', { name: 'Actions for Notes' }))
    fireEvent.click(screen.getByRole('button', { name: 'Update…' }))
    const field = await screen.findByPlaceholderText(/\/path\/to\/app\s+or\s+https:\/\/github\.com\/owner\/app\.git/)
    await userEvent.clear(field)
    await userEvent.type(field, source)
    await userEvent.click(screen.getByRole('button', { name: /^Update$/ }))
    await waitFor(() => expect(screen.getByText('v1.1.0')).toBeInTheDocument())
    expect(screen.queryByText('v1.0.0')).toBeNull()
    expect(collectionReads).toBeGreaterThan(beforeUpdate)
    expect(screen.queryByRole('button', { name: 'Update anyway' })).toBeNull()
  })

  it('does not install an interval-based collection polling loop', async () => {
    render(<AppsSection query={{ view: 'library' }} setQuery={() => {}} navigate={() => {}} />)
    await screen.findByText('v1.1.0')
    const sourceCode = readFileSync(join(process.cwd(), 'src/features/apps/AppsSection.tsx'), 'utf8')
    const ast = ts.createSourceFile('AppsSection.tsx', sourceCode, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    const collection = ast.statements.find((node): node is ts.FunctionDeclaration => ts.isFunctionDeclaration(node) && node.name?.text === 'AppsSection')
    expect(collection, 'the actual collection component must exist').toBeDefined()
    expect(collection?.getText(ast)).toContain('api.apps()')
    const timers: Array<{ owner: string; call: string }> = []
    const visit = (node: ts.Node, owner: string) => {
      if (ts.isFunctionDeclaration(node)) owner = node.name?.text ?? owner
      if (ts.isCallExpression(node)) {
        const called = node.expression
        const name = ts.isPropertyAccessExpression(called) ? called.name.text : ts.isIdentifier(called) ? called.text : ''
        if (['setInterval', 'setTimeout', 'requestAnimationFrame'].includes(name)) timers.push({ owner, call: called.getText(ast) })
      }
      ts.forEachChild(node, child => visit(child, owner))
    }
    visit(ast, '<module>')
    expect(timers, 'only the existing sidecar detail-status timer is outside the collection; new polling requires review').toEqual([{ owner: 'AppDetailPanel', call: 'window.setInterval' }])
    const detail = ast.statements.find((node): node is ts.FunctionDeclaration => ts.isFunctionDeclaration(node) && node.name?.text === 'AppDetailPanel')
    expect(detail?.getText(ast)).toContain('api.sidecarInstallStatus(app.name)')
  })
})
