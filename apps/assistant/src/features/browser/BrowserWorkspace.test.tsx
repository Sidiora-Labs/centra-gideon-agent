import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
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
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let vite: Awaited<ReturnType<typeof startViteEntryServer>> | undefined
  let browser: Awaited<ReturnType<typeof startBrowserHarness>> | undefined
  try {
    const startup = await new Promise<{ api_port: number }>((done, reject) => {
      let output = ''
      let errors = ''
      const timeout = setTimeout(() => reject(new Error(`Browser server timed out: ${errors}`)), 20000)
      native.stdout.on('data', chunk => {
        output += String(chunk)
        const line = output.split('\n')[0]
        if (!line) return
        clearTimeout(timeout)
        try { done(JSON.parse(line) as { api_port: number }) }
        catch (error) { reject(new Error(`Invalid browser server startup: ${String(error)}`)) }
      })
      native.stderr.on('data', chunk => { errors = (errors + String(chunk)).slice(-4000) })
      native.once('exit', code => { clearTimeout(timeout); reject(new Error(`Browser server exited ${code}: ${errors}`)) })
    })
    await writeFile(entryFile, `
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import { ownerScope, signInOwner } from '/src/shared/auth.web.tsx';
      import { WorkspaceFrame } from '/src/shared/shell/WorkspaceFrame.web.tsx';
      import { parseShellRoute, serializeShellRoute } from '/src/shared/shell/shellRoutes.ts';
      import BrowserRoute, { isBrowserRoute } from '/src/features/browser/BrowserRoute.web.tsx';
      import { browserModuleDefinition } from '/src/features/browser/moduleDefinitions.web.ts';
      const root = createRoot(document.getElementById('root'));
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
            return new Promise((resolve, reject) => {
              window.releasePreview = () => { window.fetch = original; original(input, init).then(resolve, reject); };
            });
          }
          return original(input, init);
        };
      };
      window.routeNow = () => parseShellRoute(location.pathname + location.search, location.origin);
      window.loaded = true;
      root.render(React.createElement(App));
    `)
    vite = await startViteEntryServer({ root: assistant, port, entryFile, apiOrigin: `http://127.0.0.1:${startup.api_port}` })
    browser = await startBrowserHarness({ windowSize: { width: 390, height: 844 } })
    await browser.navigate(`${origin}/assistant/apps?v=1&view=workspace&placement=browser%2Fsession&session=browser-chat&from=chat&fromSession=browser-chat`)
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
    expect(await browser.evaluate<string>(`[...document.querySelectorAll('[aria-label="Browser preview"]')].find(node => node.querySelector('h2')?.textContent === 'Live preview')?.textContent || ''`)).toContain('Gideon has control')
    expect(await browser.evaluate<string>(`[...document.querySelectorAll('[aria-label="Browser preview"] img')][0]?.style.pointerEvents || ''`)).toBe('none')
    await browser.evaluate('window.holdPreview()')
    await browser.evaluate("clickButton('Refresh preview', 'Live preview')")
    await browser.waitFor('typeof window.releasePreview === "function"', 'delayed native preview response')
    await browser.evaluate("clickButton('Take control')")
    await browser.waitFor("document.body.textContent.includes('You have control')", 'versioned control takeover')
    await browser.waitFor("document.body.textContent.includes('Waiting for a current preview')", 'stale preview hidden after takeover')
    expect(await browser.evaluate<boolean>(`[...document.querySelectorAll('[aria-label="Browser preview"] img')].length === 0`)).toBe(true)
    await browser.evaluate('window.releasePreview()')
    await browser.waitFor("document.body.textContent.includes('You have control')", 'late old preview ignored')
    expect(await browser.evaluate<boolean>(`[...document.querySelectorAll('[aria-label="Browser preview"] img')].length === 0`)).toBe(true)
    await browser.evaluate("clickButton('Refresh preview', 'Live preview')")
    await browser.waitFor("[...document.querySelectorAll('[aria-label=\"Browser preview\"] img')].some(image => image.complete && image.naturalWidth > 0)", 'current preview after takeover')
    expect(await browser.evaluate<number>("Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)")).toBeLessThanOrEqual(391)
    expect(await browser.evaluate<number>("document.querySelector('[aria-label=\"Browser workspace\"] button')?.getBoundingClientRect().height || 0")).toBeGreaterThanOrEqual(44)
    await browser.evaluate("clickButton('Return to conversation')")
    await browser.waitFor("location.pathname === '/assistant/chat'", 'return to original conversation route')
    expect(await browser.evaluate<string>("location.search")).toContain('session=browser-chat')
    expect(await browser.evaluate<string>("document.activeElement?.textContent || ''")).toBe('Conversation')
    expect(await browser.evaluate<string>("document.body.textContent")).toContain('Returned to conversation browser-chat')
  } finally {
    if (browser) await browser.close()
    if (vite) await vite.close()
    native.kill('SIGTERM')
    await rm(entryDirectory, { recursive: true, force: true })
  }
}, 120000)
