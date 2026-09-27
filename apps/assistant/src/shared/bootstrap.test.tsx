import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { spawn } from 'node:child_process'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterAll } from 'vitest'
import { describe, expect, it } from 'vitest'
import { startBrowserHarness, startViteEntryServer, type BrowserHarness } from '../../test-support/browserHarness'
import { startNativeServer, type NativeServer } from '../../test-support/nativeServer'
import type { ViteDevServer } from 'vite'
import { identityErrorMessage, ownerScope, signInControls } from './auth.web'
import { isIdentityDenied } from './bootstrap.web'
import { GatewayError } from './transport.web'

const repositoryRoot = process.cwd().replace(/\/apps\/assistant$/, '')
const servers: Array<{ stop: () => Promise<void> }> = []
const browsers: BrowserHarness[] = []
const viteServers: ViteDevServer[] = []
const directories: string[] = []

afterAll(async () => {
  await Promise.all(browsers.map(browser => browser.close()))
  await Promise.all(servers.map(server => server.stop()))
  await Promise.all(viteServers.map(server => server.close()))
  await Promise.all(directories.map(directory => rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })))
})

async function reservePort(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(resolve => server.close(() => resolve()))
  return port
}

async function startConversationNativeServer(origin: string): Promise<{ apiOrigin: string; stop: () => Promise<void> }> {
  const child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', [
    join(repositoryRoot, 'apps/assistant/test-support/conversation_server.py'), origin,
  ], {
    cwd: repositoryRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...Object.fromEntries(Object.entries(process.env).filter(([key]) => key !== 'OPENAI_API_KEY')),
      GIDEON_TEST_MODEL: '',
      PYTHONPATH: join(repositoryRoot, 'runtime'),
    },
  })
  let errors = ''
  child.stderr.on('data', chunk => { errors = (errors + String(chunk)).slice(-6000) })
  const startup = await new Promise<{ api_port: number }>((resolve, reject) => {
    let output = ''
    const timeout = setTimeout(() => reject(new Error(`Conversation server timed out: ${errors}`)), 30000)
    child.stdout.on('data', chunk => {
      output += String(chunk)
      const line = output.split('\n')[0]
      if (!line) return
      try {
        clearTimeout(timeout)
        resolve(JSON.parse(line) as { api_port: number })
      } catch (error) {
        clearTimeout(timeout)
        reject(new Error(`Conversation server did not emit startup JSON: ${line}; ${String(error)}`))
      }
    })
    child.once('exit', code => {
      clearTimeout(timeout)
      reject(new Error(`Conversation server exited ${code}: ${errors}`))
    })
  })
  if (typeof startup.api_port !== 'number') {
    child.kill('SIGTERM')
    throw new Error(`Conversation server omitted API port: ${JSON.stringify(startup)}`)
  }
  let stopped = false
  return {
    apiOrigin: `http://127.0.0.1:${startup.api_port}`,
    stop: async () => {
      if (stopped) return
      stopped = true
      child.kill('SIGTERM')
      await new Promise<void>(resolve => {
        if (child.exitCode !== null || child.signalCode !== null) { resolve(); return }
        child.once('exit', () => resolve())
        setTimeout(resolve, 5000)
      })
    },
  }
}

async function startBootstrapBrowser(): Promise<{ browser: BrowserHarness; native: NativeServer }> {
  const port = await reservePort()
  const origin = `http://127.0.0.1:${port}`
  const native = await startNativeServer({
    script: join(repositoryRoot, 'apps/assistant/test-support/auth_server.py'),
    origin,
    repositoryRoot,
  })
  servers.push(native)
  const directory = await mkdtemp(join(tmpdir(), 'gideon-bootstrap-test-'))
  directories.push(directory)
  const entryFile = join(directory, 'entry.tsx')
  await writeFile(entryFile, `import React, { useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { AssistantBootstrapProvider, useAssistantBootstrap } from '/src/shared/bootstrap.web.tsx';
declare global { interface Window { __refreshBootstrap?: () => Promise<void>; __cleared?: string[] } }
function Probe() {
  const { state, refresh } = useAssistantBootstrap();
  useEffect(() => { window.__refreshBootstrap = refresh }, [refresh]);
  return <p id="ready-owner">{state.phase === 'ready' ? state.owner.user : state.phase}</p>;
}
window.__cleared = [];
createRoot(document.getElementById('root')!).render(<AssistantBootstrapProvider clearOwnerCache={scope => window.__cleared?.push(scope.cacheKey)}><Probe /></AssistantBootstrapProvider>);`, 'utf8')
  viteServers.push(await startViteEntryServer({
    root: join(repositoryRoot, 'apps/assistant'),
    port,
    entryFile,
    apiOrigin: native.apiOrigin,
    route: '/bootstrap-test',
  }))
  const browser = await startBrowserHarness({ profileDirectory: join(directory, 'chromium'), windowSize: { width: 1024, height: 768 } })
  browsers.push(browser)
  await browser.navigate(`${origin}/bootstrap-test`)
  await browser.waitFor("document.querySelector('#gideon-password')", 'real owner sign-in form')
  await browser.evaluate(`(()=>{
    const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};
    set('gideon-username','owner-a');set('gideon-password','correct-horse-battery-staple');document.querySelector('form').requestSubmit();return true
  })()`)
  await browser.waitFor("document.querySelector('#ready-owner')?.textContent === 'owner-a'", 'authenticated real owner')
  return { browser, native }
}

async function startConversationAppBrowser(): Promise<{ browser: BrowserHarness; origin: string }> {
  const port = await reservePort()
  const origin = `http://127.0.0.1:${port}`
  const native = await startConversationNativeServer(origin)
  servers.push(native)
  const directory = await mkdtemp(join(repositoryRoot, 'apps/assistant/.conversation-app-entry-'))
  directories.push(directory)
  const entryFile = join(directory, 'entry.tsx')
  await writeFile(entryFile, `import { createRoot } from 'react-dom/client';
import App from '/App.tsx';
createRoot(document.getElementById('root')!).render(<App />);`, 'utf8')
  viteServers.push(await startViteEntryServer({
    root: join(repositoryRoot, 'apps/assistant'),
    port,
    entryFile,
    apiOrigin: native.apiOrigin,
  }))
  const browser = await startBrowserHarness({
    profileDirectory: join(directory, 'chromium'),
    windowSize: { width: 1280, height: 900 },
  })
  browsers.push(browser)
  await browser.navigate(`${origin}/assistant/chat?v=1&from=activity&fromSession=origin-session&fromSelection=return-message&fromScroll=320`)
  await browser.waitFor("document.querySelector('#gideon-password')", 'real owner sign-in form')
  await browser.evaluate(`(()=>{
    const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};
    set('gideon-username','conversation-owner');set('gideon-password','correct-horse-battery-staple');document.querySelector('form').requestSubmit();return true
  })()`)
  await browser.waitFor("document.getElementById('gideon-message-composer')", 'real App chat composer')
  return { browser, origin }
}

describe('assistant owner bootstrap', () => {
  it('offers password and authenticator controls when configured or required by login', () => {
    expect(signInControls({ login_enabled: true, totp_required: true }))
      .toEqual({ password: true, totp: true })
    expect(signInControls({ login_enabled: true, totp_required: false }))
      .toEqual({ password: true, totp: false })
    expect(signInControls({ login_enabled: true, totp_required: false }, true))
      .toEqual({ password: true, totp: true })
  })

  it('offers retry without a password form when password login is disabled', () => {
    expect(signInControls({ login_enabled: false, totp_required: false }))
      .toEqual({ password: false, totp: false })
  })

  it('distinguishes expired identity from denied operations and explains sign-in errors', () => {
    expect(isIdentityDenied(new GatewayError('Unauthorized', 401))).toBe(true)
    expect(isIdentityDenied(new GatewayError('Forbidden', 403))).toBe(false)
    expect(isIdentityDenied(new GatewayError('Session expired', 403, '', undefined, true))).toBe(true)
    expect(isIdentityDenied(new GatewayError('Wrong origin', 403, 'auth_origin_not_allowed'))).toBe(false)
    expect(isIdentityDenied(new GatewayError('Failed', 500))).toBe(false)
    expect(identityErrorMessage(new GatewayError('code needed', 401, 'auth_totp_required')))
      .toContain('authenticator app')
    expect(identityErrorMessage(new GatewayError('locked', 429, 'auth_locked_out', 12)))
      .toContain('12 seconds')
  })

  it('scopes owner data to the runtime origin and authenticated server identity', () => {
    const first = ownerScope('https://gideon.example:9443/path', { user: 'owner-a' })
    expect(first).toEqual(ownerScope('https://gideon.example:9443/other', { user: 'owner-a' }))
    expect(first.runtimeOrigin).toBe('https://gideon.example:9443')
    expect(first.cacheKey).not.toBe(ownerScope('https://gideon.example:9443', { user: 'owner-b' }).cacheKey)
    expect(first.cacheKey).not.toBe(ownerScope('https://other.example:9443', { user: 'owner-a' }).cacheKey)
    expect(() => ownerScope('https://gideon.example', { user: '' })).toThrow(TypeError)
  })

  it('preserves owner cache through an interrupted real API refresh', async () => {
    const { browser, native } = await startBootstrapBrowser()
    await native.stop()
    await browser.evaluate('window.__refreshBootstrap?.()')
    await browser.waitFor("document.querySelector('#gideon-password') && document.querySelector('[role=alert]')", 'unavailable refresh state')
    const state = await browser.evaluate<{ owner: string | null; cleared: string[] }>(`({ owner: document.querySelector('#ready-owner')?.textContent || null,
      cleared: window.__cleared || [] })`)
    expect(state.owner).toBeNull()
    expect(state.cleared).toEqual([])
  }, 60000)

  it('clears owner cache when the real gateway revokes the authenticated session', async () => {
    const { browser, native } = await startBootstrapBrowser()
    const response = await fetch(`${native.controlOrigin}/revoke`, { method: 'POST' })
    expect(response.ok).toBe(true)
    await browser.evaluate('window.__refreshBootstrap?.()')
    await browser.waitFor("document.querySelector('#gideon-password') && !document.querySelector('#ready-owner')", 'authoritative session loss')
    const cleared = await browser.evaluate('window.__cleared || []')
    expect(cleared).toHaveLength(1)
  }, 60000)

  it('puts an App-created native session in the chat URL and restores it after reload', async () => {
    const { browser } = await startConversationAppBrowser()
    await browser.evaluate(`(()=>{
      const input=document.getElementById('gideon-message-composer');
      const set=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input),'value').set;
      set.call(input,'Create a persistent Gideon session');input.dispatchEvent(new Event('input',{bubbles:true}));
      document.querySelector('[aria-label="Send message"]')?.click();return true
    })()`)
    await browser.waitFor(`new URLSearchParams(location.search).get('recordKind')==='chat_session'
      && !!new URLSearchParams(location.search).get('recordId')`, 'new canonical chat detail URL', 25000)
    const route = await browser.evaluate<{ href: string; key: string; returnTo: string | null; selection: string | null; scroll: string | null }>(`(()=>{
      const params=new URLSearchParams(location.search);
      return {href:location.href,key:params.get('recordId'),returnTo:params.get('from'),selection:params.get('fromSelection'),scroll:params.get('fromScroll')};
    })()`)
    expect(route.key).toBeTruthy()
    expect(route.returnTo).toBe('activity')
    expect(route.selection).toBe('return-message')
    expect(route.scroll).toBe('320')
    const apiHeaders = JSON.stringify({ 'X-Gideon-API-Version': '1', 'X-Session-Key': 'dashboard:ui' })
    const nativeSession = await browser.evaluate<{ key: string }>(`fetch('/api/chat/sessions/'+encodeURIComponent(${JSON.stringify(route.key)}),
      {credentials:'same-origin',headers:${apiHeaders}}).then(async response=>{
        if(!response.ok)throw Error('Native session read failed: '+response.status);return response.json()
      })`)
    expect(nativeSession.key).toBe(route.key)

    await browser.command('Page.reload', { ignoreCache: true })
    await browser.waitFor(`location.href===${JSON.stringify(route.href)}
      && document.querySelector('#gideon-message-composer:not([disabled])')
      && document.querySelector('[aria-label="Return to previous workspace"]')`, 'same native session and return context after reload', 25000)
    expect(await browser.evaluate("new URLSearchParams(location.search).get('recordId')")).toBe(route.key)
  }, 90000)
})
