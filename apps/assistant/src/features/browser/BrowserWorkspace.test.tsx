import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { expect, it } from 'vitest'
import { startBrowserHarness, startViteEntryServer } from '../../../test-support/browserHarness'

async function freePort(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return port
}

it('keeps authenticated browser previews bound to the current owner, version and route on a phone viewport', async () => {
  const repository = resolve(process.cwd(), '../..')
  const assistant = join(repository, 'apps/assistant')
  const entryDirectory = await mkdtemp(join(assistant, '.browser-preview-'))
  const entryFile = join(entryDirectory, 'entry.tsx')
  const port = await freePort()
  const origin = `http://127.0.0.1:${port}`
  const python = process.env.GIDEON_TEST_PYTHON || 'python3'
  const { spawn } = await import('node:child_process')
  const native = spawn(python, [join(assistant, 'test-support/browser_server.py'), origin], {
    cwd: repository,
    env: { ...process.env, PYTHONPATH: join(repository, 'runtime') },
    stdio: ['pipe', 'pipe', 'pipe'],
  })
  const nativeLines = createInterface({ input: native.stdout })
  let nativeErrors = ''
  native.stderr.on('data', chunk => { nativeErrors = (nativeErrors + String(chunk)).slice(-4000) })
  function readNativeLine(label: string): Promise<Record<string, unknown>> {
    return new Promise((done, reject) => {
      const timeout = setTimeout(() => finish(new Error(`${label} timed out: ${nativeErrors}`)), 20000)
      const onLine = (line: string) => {
        try { finish(undefined, JSON.parse(line) as Record<string, unknown>) }
        catch (error) { finish(new Error(`Invalid ${label}: ${String(error)}`)) }
      }
      const onExit = (code: number | null) => finish(new Error(`Browser server exited ${code}: ${nativeErrors}`))
      function finish(error?: Error, value?: Record<string, unknown>) {
        clearTimeout(timeout)
        nativeLines.off('line', onLine)
        native.off('exit', onExit)
        if (error) reject(error)
        else done(value || {})
      }
      nativeLines.on('line', onLine)
      native.once('exit', onExit)
    })
  }
  let vite: Awaited<ReturnType<typeof startViteEntryServer>> | undefined
  let browser: Awaited<ReturnType<typeof startBrowserHarness>> | undefined
  try {
    const startup = await readNativeLine('browser server startup')
    if (typeof startup.api_port !== 'number') throw new Error('Browser server omitted its API port')
    await writeFile(entryFile, `
      import React from 'react';
      import { flushSync } from 'react-dom';
      import { createRoot } from 'react-dom/client';
      import { ownerScope, signInOwner } from '/src/shared/auth.web.tsx';
      import { WorkspaceFrame } from '/src/shared/shell/WorkspaceFrame.web.tsx';
      import { parseShellRoute, serializeShellRoute } from '/src/shared/shell/shellRoutes.ts';
      import BrowserRoute, { isBrowserRoute } from '/src/features/browser/BrowserRoute.web.tsx';
      import BrowserPreview from '/src/features/browser/BrowserPreview.web.tsx';
      import { BrowserClient } from '/src/features/browser/browserClient.ts';
      import { browserModuleDefinition } from '/src/features/browser/moduleDefinitions.web.ts';
      const root = createRoot(document.getElementById('root'));
      function DirectPreview({ session }) {
        const [scope, setScope] = React.useState(window.scope);
        const client = React.useMemo(() => new BrowserClient(scope), [scope.cacheKey]);
        window.switchPreviewScope = next => flushSync(() => setScope(next));
        return React.createElement(BrowserPreview, { client, session });
      }
      function App() {
        const [route, setRoute] = React.useState(() => parseShellRoute(location.pathname + location.search, location.origin));
        window.currentRoute = route;
        const navigate = next => { window.currentRoute = next; history.pushState(null, '', serializeShellRoute(next)); setRoute(next); };
        React.useEffect(() => { const pop = () => setRoute(parseShellRoute(location.pathname + location.search, location.origin)); window.addEventListener('popstate', pop); return () => window.removeEventListener('popstate', pop); }, []);
        if (!window.scope) return React.createElement('p', null, 'Sign in to continue');
        if (isBrowserRoute(route)) return React.createElement(BrowserRoute, { route, scope: window.scope, navigate });
        return React.createElement(WorkspaceFrame, { route, mode: 'full', title: 'Conversation', state: { kind: 'ready' } },
          React.createElement('p', null, 'Returned to conversation ' + (route.sessionId || '')));
      }
      window.signIn = async () => { window.scope = ownerScope(location.origin, await signInOwner('browser-owner', 'correct-horse-battery-staple')); root.render(React.createElement(App)); return window.scope.ownerId; };
      window.resolveBrowser = async () => browserModuleDefinition.resolve(window.scope, window.currentRoute);
      window.mountDirectPreview = async () => {
        const opened = await new BrowserClient(window.scope).open('browser-chat');
        if (opened.state !== 'ready') throw new Error(opened.message);
        window.directSession = opened.value;
        root.render(React.createElement(DirectPreview, { session: opened.value }));
        return opened.value;
      };
      window.switchToOtherOwner = async () => {
        const signedIn = await signInOwner('browser-other', 'other-correct-horse-battery-staple');
        const session = await fetch('/api/auth/session').then(response => response.json());
        if (session.user !== signedIn.user) throw new Error('Owner sign-in and native session disagree');
        const nextScope = ownerScope(location.origin, session);
        window.switchPreviewScope(nextScope);
        return { user: session.user,
          masked: document.querySelector('[aria-label="Browser preview"] img') === null &&
            document.body.textContent.includes('Waiting for a current preview') };
      };
      window.clickButton = (label, area) => {
        const container = area ? [...document.querySelectorAll('[aria-label="Browser preview"]')].find(node => node.querySelector('h2')?.textContent === area) : document;
        const button = [...(container || document).querySelectorAll('button')].find(node => node.textContent.trim() === label && !node.disabled);
        if (!button) throw new Error('Missing enabled button: ' + label);
        button.click();
      };
      window.holdPreview = () => {
        const original = window.fetch.bind(window);
        let held = false;
        window.fetch = (input, init) => {
          const url = new URL(typeof input === 'string' ? input : input.url, location.origin);
          if (!held && url.pathname.endsWith('/preview')) {
            held = true;
            return original(input, init).then(response => new Promise(resolve => {
              window.releasePreview = () => { window.fetch = original; resolve(response); };
            }));
          }
          return original(input, init);
        };
      };
      window.routeNow = () => parseShellRoute(location.pathname + location.search, location.origin);
      window.displayedPreviewIs = (version, holder) =>
        [...document.querySelectorAll('[aria-label="Browser preview"] img')]
          .every(image => Number(image.dataset.version) === version && image.dataset.controlHolder === holder);
      window.loaded = true;
      root.render(React.createElement(App));
    `)
    vite = await startViteEntryServer({ root: assistant, port, entryFile, apiOrigin: `http://127.0.0.1:${startup.api_port}` })
    browser = await startBrowserHarness({ windowSize: { width: 390, height: 844 } })
    await browser.navigate(`${origin}/assistant/apps?v=1&view=workspace&placement=browser%2Fsession&session=browser-chat&from=chat&fromSession=browser-chat`)
    await vite.waitForRequestsIdle()
    await browser.waitFor("window.loaded === true", 'browser route app loaded')
    expect(await browser.evaluate<string>('window.signIn()')).toBe('browser-owner')
    await browser.waitFor("document.body.textContent.includes('Ready to connect')", 'owner browser reservation')
    const resolved = await browser.evaluate<string>('window.resolveBrowser()')
    expect(resolved).toBe('available')
    const identity = await browser.evaluate<string>(`document.body.textContent.match(/Session ([^\\s]+)/)?.[1] || ''`)
    expect(identity).not.toBe('')
    expect(await browser.evaluate<boolean>(`window.routeNow().returnTo?.sessionId === 'browser-chat'`)).toBe(true)
    await browser.evaluate("clickButton('Connect browser')")
    await browser.waitFor("document.body.textContent.includes('Live preview')", 'active browser preview')
    await browser.waitFor("document.querySelector('[aria-label=\"Browser preview\"] img')?.complete && document.querySelector('[aria-label=\"Browser preview\"] img')?.naturalWidth > 0", 'real preview image')
    const versionBeforeTakeover = await browser.evaluate<number>("Number(document.querySelector('[aria-label=\"Browser preview\"] img')?.dataset.version)")
    expect(await browser.evaluate<string>(`[...document.querySelectorAll('[aria-label="Browser preview"]')].find(node => node.querySelector('h2')?.textContent === 'Live preview')?.textContent || ''`)).toContain('Gideon has control')
    expect(await browser.evaluate<string>(`[...document.querySelectorAll('[aria-label="Browser preview"] img')][0]?.style.pointerEvents || ''`)).toBe('none')
    await browser.evaluate("Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })")
    await browser.evaluate('window.holdPreview()')
    await browser.evaluate("clickButton('Refresh preview', 'Live preview')")
    await browser.waitFor('typeof window.releasePreview === "function"', 'delayed native preview response')
    await browser.evaluate("clickButton('Take control')")
    await browser.waitFor("document.body.textContent.includes('You have control')", 'versioned control takeover')
    await browser.waitFor(`window.displayedPreviewIs(${versionBeforeTakeover + 1}, 'customer')`, 'only current takeover preview shown')
    expect(await browser.evaluate<boolean>(`window.displayedPreviewIs(${versionBeforeTakeover + 1}, 'customer')`)).toBe(true)
    await browser.evaluate('window.releasePreview()')
    await browser.waitFor(`window.displayedPreviewIs(${versionBeforeTakeover + 1}, 'customer')`, 'late old preview ignored')
    expect(await browser.evaluate<boolean>(`window.displayedPreviewIs(${versionBeforeTakeover + 1}, 'customer')`)).toBe(true)
    await browser.evaluate("clickButton('Refresh preview', 'Live preview')")
    await browser.waitFor(`window.displayedPreviewIs(${versionBeforeTakeover + 1}, 'customer') &&
      [...document.querySelectorAll('[aria-label="Browser preview"] img')].some(image => image.complete && image.naturalWidth > 0)`, 'current preview after takeover')
    expect(await browser.evaluate<number>("Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)")).toBeLessThanOrEqual(391)
    expect(await browser.evaluate<number>("document.querySelector('[aria-label=\"Browser workspace\"] button')?.getBoundingClientRect().height || 0")).toBeGreaterThanOrEqual(44)
    await browser.evaluate("clickButton('Return to conversation')")
    await browser.waitFor("location.pathname === '/assistant/chat'", 'return to original conversation route')
    expect(await browser.evaluate<string>("location.search")).toContain('session=browser-chat')
    expect(await browser.evaluate<string>("document.activeElement?.textContent || ''")).toBe('Conversation')
    expect(await browser.evaluate<string>("document.body.textContent")).toContain('Returned to conversation browser-chat')

    const directSession = await browser.evaluate<{ id: string; version: number; controlHolder: string }>('window.mountDirectPreview()')
    await browser.waitFor("document.querySelector('[aria-label=\"Browser preview\"] img')?.complete && document.querySelector('[aria-label=\"Browser preview\"] img')?.naturalWidth > 0", 'current-owner direct preview')
    await browser.evaluate('window.holdPreview()')
    await browser.evaluate("clickButton('Refresh preview', 'Live preview')")
    await browser.waitFor('typeof window.releasePreview === "function"', 'owner preview response received and held')
    expect(await browser.evaluate<string>("fetch('/api/auth/session').then(response => response.json()).then(session => session.user)")).toBe('browser-owner')
    const rotated = readNativeLine('browser owner rotation')
    native.stdin.write(`${JSON.stringify({ rotate_owner: 'browser-other' })}\n`)
    expect((await rotated).owner_rotated).toBe('browser-other')
    expect(await browser.evaluate<{ user: string; masked: boolean }>('window.switchToOtherOwner()'))
      .toEqual({ user: 'browser-other', masked: true })
    expect(await browser.evaluate<string>("fetch('/api/auth/session').then(response => response.json()).then(session => session.user)")).toBe('browser-other')
    expect(await browser.evaluate<boolean>("document.querySelector('[aria-label=\"Browser preview\"] img') === null")).toBe(true)
    await browser.evaluate('window.releasePreview()')
    await browser.waitFor("document.body.textContent.includes('This conversation or browser is unavailable.')", 'new owner denied the old session')
    expect(await browser.evaluate<boolean>(`document.querySelector('[aria-label="Browser preview"] img') === null`)).toBe(true)
    expect(await browser.evaluate<{ id: string; version: number; controlHolder: string }>(
      '({ id: window.directSession?.id, version: window.directSession?.version, controlHolder: window.directSession?.controlHolder })'))
      .toEqual({ id: directSession.id, version: directSession.version, controlHolder: directSession.controlHolder })
  } finally {
    if (browser) await browser.close()
    if (vite) await vite.close()
    native.kill('SIGTERM')
    await rm(entryDirectory, { recursive: true, force: true })
  }
}, 120000)
