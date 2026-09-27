import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer as createNetServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { startBrowserHarness, startViteEntryServer, type BrowserHarness } from "../../../test-support/browserHarness";
import { DESTINATIONS } from "./destinations";
import { MINIAPPS, miniappStateMessage } from "./miniapps";
import { discoveryStorageKey } from "./discoveryState";

const root = resolve(process.cwd(), "../..");
const children: ChildProcessWithoutNullStreams[] = [];
const directories: string[] = [];
let vite: Awaited<ReturnType<typeof startViteEntryServer>> | undefined;
let browser: BrowserHarness | undefined;

afterAll(async () => {
  await browser?.close();
  for (const child of children) {
    child.kill("SIGTERM");
    await new Promise<void>(resolveExit => {
      if (child.exitCode !== null || child.signalCode !== null) { resolveExit(); return; }
      child.once("close", () => resolveExit());
      setTimeout(resolveExit, 5000);
    });
  }
  await vite?.close();
  for (const directory of directories) await rm(directory, { recursive: true, force: true });
});

async function availablePort(): Promise<number> {
  const server = createNetServer();
  await new Promise<void>(done => server.listen(0, "127.0.0.1", done));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise<void>(done => server.close(() => done()));
  return port;
}

const names = [
  "Research", "Slides", "Studio", "Writer", "Music", "Worlds", "Knowledge", "Journal", "Health",
  "People", "Compass", "Automations", "Code", "Agents", "Lab", "Workspace", "Connections",
];

const nativeStudioServer = String.raw`
import asyncio, json, os, sys, tempfile, time
from pathlib import Path
from aiohttp import web

async def main(origin):
    with tempfile.TemporaryDirectory(prefix='gideon-miniapps-studio-') as directory:
        home = Path(directory)
        os.environ['GIDEON_HOME'] = str(home)
        (home / 'config.json').write_text(json.dumps({'auth': {'login_enabled': True}, 'dashboard': {'document_editing': True}}), encoding='utf-8')
        from gideon.cognition.history import ConversationLog
        from gideon.core.config.loader import AppConfig
        from gideon.engine.session import ConversationDirectory
        from gideon.interfaces.dashboard import token_auth
        from gideon.security.auth import credentials
        from gideon.interfaces.dashboard.handlers import auth
        from gideon.interfaces.dashboard.state import ConsoleState
        from gideon.workspace.artifacts import registry
        from gideon.workspace.artifacts.native import NativeArtifactProvider
        from gideon.workspace.artifacts.handlers import register_artifact_routes
        credentials.set_password('studio-owner', 'correct-horse-battery-staple')
        token_auth.use_ephemeral_secret()
        registry.register_provider(NativeArtifactProvider(home / 'artifacts'))
        state = ConsoleState(
            sessions=ConversationDirectory(AppConfig.load()),
            start_time=time.time(),
            conversation_log=ConversationLog(base_dir=home / 'history'),
            owner_id='studio-owner',
        )
        artifact_release = asyncio.Event()
        artifact_release.set()
        artifact_entered = asyncio.Event()
        artifact_finished = asyncio.Event()
        @web.middleware
        async def hold_artifact_list(request, handler):
            if request.method == 'GET' and request.path == '/api/artifacts' and request.query.get('kind') == 'pptx':
                if not artifact_release.is_set():
                    artifact_entered.set()
                    await artifact_release.wait()
                response = await handler(request)
                artifact_finished.set()
                return response
            return await handler(request)
        app = web.Application(middlewares=[token_auth.token_auth_middleware(port=10000), hold_artifact_list])
        app['port'] = 10000
        app['allowed_origins'] = {origin}
        app['state'] = state
        app.router.add_post('/api/auth/login', auth.api_auth_login)
        app.router.add_get('/api/auth/session', auth.api_auth_session)
        register_artifact_routes(app)
        control = web.Application()
        async def arm_hold(_request):
            artifact_entered.clear()
            artifact_finished.clear()
            artifact_release.clear()
            return web.json_response({'armed': True})
        async def release_hold(_request):
            artifact_release.set()
            return web.json_response({'released': True})
        async def hold_status(_request):
            return web.json_response({'entered': artifact_entered.is_set(), 'finished': artifact_finished.is_set()})
        control.router.add_post('/hold', arm_hold)
        control.router.add_post('/release', release_hold)
        control.router.add_get('/status', hold_status)
        runner = web.AppRunner(app)
        control_runner = web.AppRunner(control)
        await runner.setup()
        await control_runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        control_site = web.TCPSite(control_runner, '127.0.0.1', 0)
        await site.start()
        await control_site.start()
        print(json.dumps({'api_port': site._server.sockets[0].getsockname()[1], 'control_port': control_site._server.sockets[0].getsockname()[1]}), flush=True)
        try: await asyncio.Event().wait()
        finally:
            await runner.cleanup()
            await control_runner.cleanup()

asyncio.run(main(sys.argv[1]))
`;

describe("named Gideon miniapps", () => {
  it("keeps all 17 names, categories, canonical targets, first actions and route ownership stable", () => {
    expect(MINIAPPS.map(app => app.name)).toEqual(names);
    expect(MINIAPPS.map(app => app.id)).toEqual([
      "research", "slides", "studio", "writer", "music", "worlds", "knowledge", "journal", "health",
      "people", "compass", "automations", "code", "agents", "lab", "workspace", "connections",
    ]);
    for (const app of MINIAPPS) {
      const destination = DESTINATIONS.find(entry => entry.id === app.destinationId);
      expect(destination, app.name).toBeDefined();
      expect(app.category, app.name).toBe(destination?.category);
      expect(app.route.placement?.id, app.name).toBe(app.destinationId);
      expect(app.firstAction.trim(), app.name).not.toBe("");
    }
    expect(MINIAPPS.find(app => app.id === "slides")?.route.placement?.subview).toBe("/slides");
    expect(MINIAPPS.find(app => app.id === "research")?.destinationId).toBe("knowledge/reports");
    expect(MINIAPPS.find(app => app.id === "compass")?.route).toMatchObject({
      destination: "goals", placement: { id: "capabilities/identity/goals" },
    });
    expect(new Set(MINIAPPS.map(app => app.destinationId)).size).toBe(17);
  });

  it("keeps pending, checking, denial and unavailable labels honest", () => {
    expect(miniappStateMessage("pending", "Research")).toContain("not been checked");
    expect(miniappStateMessage("checking", "Research")).toContain("owning workspace");
    expect(miniappStateMessage("denied", "Research")).toContain("cannot open");
    expect(miniappStateMessage("unavailable", "Research")).toContain("could not be checked");
  });

  it("checks a native Slides action before opening it and blocks a stale owner scope", async () => {
    const port = await availablePort();
    const origin = `http://127.0.0.1:${port}`;
    const api = spawn(process.env.GIDEON_TEST_PYTHON || "python3",
      ["-u", "-c", nativeStudioServer, origin], {
        cwd: root,
        env: { ...process.env, PYTHONPATH: join(root, "runtime") },
      });
    children.push(api);
    const startup = await new Promise<{ api_port: number; control_port: number }>((done, fail) => {
      let stdout = "";
      let stderr = "";
      const timer = setTimeout(() => fail(new Error(`Native Studio server did not start: ${stderr}`)), 20000);
      api.stdout.on("data", chunk => {
        stdout += String(chunk);
        if (stdout.includes("\n")) { clearTimeout(timer); done(JSON.parse(stdout.split("\n")[0]) as { api_port: number; control_port: number }); }
      });
      api.stderr.on("data", chunk => { stderr = (stderr + String(chunk)).slice(-4000); });
      api.once("exit", code => { clearTimeout(timer); fail(new Error(`Native Studio server exited ${code}: ${stderr}`)); });
    });
    const entryDirectory = await mkdtemp(join(root, "apps/assistant/.miniapps-entry-"));
    directories.push(entryDirectory);
    const entryFile = join(entryDirectory, "miniapps-entry.tsx");
    await writeFile(entryFile, `import React from 'react';
import { createRoot } from 'react-dom/client';
import { signInOwner } from '/src/shared/auth.web.tsx';
import { createShellRoute, serializeShellRoute } from '/src/shared/shell/shellRoutes.ts';
import { moduleForRoute } from '/src/shared/shell/webModules.web.tsx';
import { ShellThemeProvider } from '/src/shared/shell/shellTheme.web.ts';
import AppsScreen from '/src/features/discovery/AppsScreen.tsx';
import { ownerScope } from '/src/shared/auth.web.tsx';

function Journey() {
  const [identity, setIdentity] = React.useState(null);
  const [scopeOwner, setScopeOwner] = React.useState('studio-owner');
  const appsRoute = () => createShellRoute('apps', { view: 'workspace', placement: { id: 'apps', query: { category: 'Create', search: 'slide' } }, returnTo: { destination: 'chat' } });
  const [route, setRoute] = React.useState(appsRoute);
  const [loaded, setLoaded] = React.useState(null);
  const scope = ownerScope(location.origin, { user: scopeOwner });
  React.useEffect(() => { void signInOwner('studio-owner', 'correct-horse-battery-staple').then(setIdentity); }, []);
  React.useEffect(() => {
    const changeOwner = event => setScopeOwner(event.detail);
    document.querySelector('#root').addEventListener('miniapp-owner', changeOwner);
    return () => document.querySelector('#root').removeEventListener('miniapp-owner', changeOwner);
  }, []);
  React.useEffect(() => {
    history.replaceState(null, '', serializeShellRoute(route));
    if (!identity || (route.destination === 'apps' && route.placement?.id === 'apps')) { setLoaded(null); return; }
    const definition = moduleForRoute(route);
    let active = true;
    if (!definition) { setLoaded({ error: 'No family workspace is registered.' }); return () => { active = false; }; }
    void definition.resolve(ownerScope(location.origin, identity), route).then(async result => {
      if (result !== 'available') return { error: 'The owning workspace is unavailable.' };
      return definition.load();
    }).then(result => { if (active) setLoaded(result); });
    return () => { active = false; };
  }, [identity, route]);
  const navigate = next => { window.__lastMiniappRoute = next; setRoute(next); };
  const returnToApps = () => { setRoute(appsRoute()); };
  if (!identity) return <p role="status">Signing in to the native Studio account…</p>;
  if (route.destination === 'apps' && route.placement?.id === 'apps') return <AppsScreen route={route} scope={scope}
    navigate={navigate} onReturn={() => setRoute(createShellRoute('chat'))} />;
  if (loaded?.error) return <p role="alert">{loaded.error}</p>;
  if (!loaded?.default) return <p role="status">Opening the owning workspace…</p>;
  return React.createElement(loaded.default, { route, scope, navigate, returnTo: route.returnTo, onReturn: returnToApps });
}

createRoot(document.getElementById('root')).render(<ShellThemeProvider initialPreference="light"><Journey /></ShellThemeProvider>);
`, "utf8");
    vite = await startViteEntryServer({ root: join(root, "apps/assistant"), port, entryFile,
      apiOrigin: `http://127.0.0.1:${startup.api_port}` });
    browser = await startBrowserHarness();
    await browser.navigate(`${origin}/assistant`);
    await browser.waitFor("document.querySelector('[aria-label=\"Gideon miniapps\"] li')", "named miniapps collection");
    expect(await browser.evaluate<string[]>("[...document.querySelectorAll('[aria-label=\"Gideon miniapps\"] li')].map(card=>card.querySelector(':scope > strong')?.textContent)"))
      .toEqual(names);
    expect(await browser.evaluate<boolean>("[...document.querySelectorAll('[aria-label=\"Gideon miniapps\"] li')].every(card => card.querySelector('[role=status]')?.textContent.includes('not been checked') || card.querySelector('button')?.disabled)"))
      .toBe(true);

    await browser.evaluate("document.querySelector('[data-miniapp-pin=health]')?.click()");
    await browser.waitFor("document.querySelector('[data-miniapp-pin=health]')?.getAttribute('aria-pressed') === 'true'", "health pin committed by React");
    expect(await browser.evaluate<boolean>("document.querySelector('[data-miniapp-pin=health]')?.getAttribute('aria-pressed') === 'true'")).toBe(true);
    const ownerStorageKey = discoveryStorageKey(JSON.stringify([origin, "studio-owner"]));
    expect(await browser.evaluate<string>(`localStorage.getItem(${JSON.stringify(ownerStorageKey)})`)).not.toContain("Health");
    const initialTimeOrigin = await browser.evaluate<number>("performance.timeOrigin");
    await browser.command("Page.reload");
    await browser.waitFor(`performance.timeOrigin !== ${initialTimeOrigin} && document.querySelector('[aria-label="Gideon miniapps"] li')`, "pinned preference after document reload");
    await browser.waitFor("document.querySelector('[data-miniapp-pin=health]')?.getAttribute('aria-pressed') === 'true'", "owner preference read after reload");
    expect(await browser.evaluate<boolean>("document.querySelector('[data-miniapp-pin=health]')?.getAttribute('aria-pressed') === 'true'")).toBe(true);
    await browser.evaluate("document.querySelector('#root').dispatchEvent(new CustomEvent('miniapp-owner', {detail:'other-owner'}))");
    await browser.waitFor("document.querySelector('[data-miniapp-pin=health]')?.getAttribute('aria-pressed') === 'false'", "other account preferences masked");
    await browser.evaluate("document.querySelector('#root').dispatchEvent(new CustomEvent('miniapp-owner', {detail:'studio-owner'}))");
    await browser.waitFor("document.querySelector('[data-miniapp-pin=health]')?.getAttribute('aria-pressed') === 'true'", "original account preferences restored");

    const controlOrigin = `http://127.0.0.1:${startup.control_port}`;
    expect((await fetch(`${controlOrigin}/hold`, { method: "POST" })).ok).toBe(true);
    await browser.evaluate("performance.clearResourceTimings(); document.querySelector('[data-miniapp-open=slides]')?.click()");
    const waitForNative = async (field: "entered" | "finished") => {
      const deadline = Date.now() + 10000;
      while (Date.now() < deadline) {
        const state = await (await fetch(`${controlOrigin}/status`)).json() as { entered: boolean; finished: boolean };
        if (state[field]) return;
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      throw new Error(`Native Slides ${field} state was not observed`);
    };
    await waitForNative("entered");
    await browser.evaluate("document.querySelector('[data-miniapp-open=knowledge]')?.click()");
    await browser.waitFor("document.querySelector('[data-miniapp-open=knowledge]')?.parentElement?.querySelector('[role=status]')?.textContent.includes('could not be checked right now')", "newer native Knowledge request unavailable");
    expect(await browser.evaluate<boolean>("performance.getEntriesByType('resource').some(entry => entry.name.includes('/api/knowledge/items?limit=100') && entry.responseEnd > 0)")).toBe(true);
    expect((await fetch(`${controlOrigin}/release`, { method: "POST" })).ok).toBe(true);
    await waitForNative("finished");
    await browser.waitFor("performance.getEntriesByType('resource').some(entry => entry.name.includes('/api/artifacts?kind=pptx') && entry.responseEnd > 0)", "older native Slides response completed");
    await browser.waitFor("document.querySelector('[data-miniapp-open=slides]')?.disabled === false", "newer Knowledge result leaves Slides action enabled");
    expect(await browser.evaluate<string>("location.pathname")).toBe("/assistant/apps");
    expect(await browser.evaluate<boolean>("document.querySelector('[data-miniapp-open=slides]')?.disabled === false")).toBe(true);
    expect(await browser.evaluate<string>(`localStorage.getItem(${JSON.stringify(ownerStorageKey)})`)).toContain('"recentIds":[]');

    expect((await fetch(`${controlOrigin}/hold`, { method: "POST" })).ok).toBe(true);
    await browser.evaluate("performance.clearResourceTimings(); document.querySelector('[data-miniapp-open=slides]')?.click()");
    await waitForNative("entered");
    await browser.evaluate("document.querySelector('[data-miniapp-pin=research]')?.click()");
    await browser.waitFor("document.querySelector('[data-miniapp-pin=research]')?.getAttribute('aria-pressed') === 'true'", "Research pin committed while Slides readiness is held");
    expect(await browser.evaluate<string>(`localStorage.getItem(${JSON.stringify(ownerStorageKey)})`)).toContain('"research"');
    expect((await fetch(`${controlOrigin}/release`, { method: "POST" })).ok).toBe(true);
    await waitForNative("finished");
    await browser.waitFor("document.querySelector('[aria-label=\"New presentation name\"]')", "held Slides launch opens after pin update");
    expect(await browser.evaluate<{ pinnedIds: string[]; recentIds: string[] }>(`JSON.parse(localStorage.getItem(${JSON.stringify(ownerStorageKey)}))`))
      .toMatchObject({ pinnedIds: ["health", "research"], recentIds: ["slides"] });

    await browser.evaluate("document.querySelector('[data-miniapp-open=slides]')?.click()");
    await browser.waitFor("document.querySelector('[aria-label=\"New presentation name\"]')", "native Slides first-action workspace");
    expect(await browser.evaluate<{ destination: string; placement: { id: string; query: Record<string, string> }; selectionId: string }>("window.__lastMiniappRoute.returnTo"))
      .toEqual({ destination: "apps", placement: { id: "apps", query: { category: "Create", search: "slide" } }, selectionId: "slides" });
    await browser.evaluate(`(() => {
      const write = (selector, value) => {
        const field = document.querySelector(selector);
        const setter = Object.getOwnPropertyDescriptor(field.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value').set;
        setter.call(field, value); field.dispatchEvent(new Event('input', { bubbles: true }));
      };
      write('[aria-label="New presentation name"]', 'Miniapp native journey');
      write('[aria-label="New presentation outline"]', '## Decision\\n- Native first action');
    })()`);
    await browser.waitFor("[...document.querySelectorAll('button')].some(button=>button.textContent.includes('Create presentation (1 slides)') && !button.disabled)", "Slides first-action form ready");
    await browser.evaluate("[...document.querySelectorAll('button')].find(button=>button.textContent.includes('Create presentation (1 slides)'))?.click()");
    await browser.waitFor("document.body.textContent.includes('saved version 1')", "native presentation created from the miniapp");

    await browser.evaluate("[...document.querySelectorAll('button')].find(button=>button.getAttribute('aria-label')==='Back')?.click()");
    await browser.waitFor("document.querySelector('[aria-label=\"Gideon miniapps\"] li')", "return to Apps");
    await browser.waitFor("document.activeElement?.getAttribute('data-miniapp-open') === 'slides'", "focus restored to the invoking miniapp control");
    expect(await browser.evaluate<string>(`localStorage.getItem(${JSON.stringify(ownerStorageKey)})`)).toContain('"recentIds":["slides"]');
    expect(await browser.evaluate<boolean>("document.querySelector('[data-miniapp-open=slides]')?.parentElement?.textContent.includes('Recently opened')")).toBe(true);
    const returnTimeOrigin = await browser.evaluate<number>("performance.timeOrigin");
    await browser.command("Page.reload");
    await browser.waitFor(`performance.timeOrigin !== ${returnTimeOrigin} && document.querySelector('[aria-label="Gideon miniapps"] li')`, "persisted launch and pin after document reload");
    await browser.waitFor("document.querySelector('[data-miniapp-pin=research]')?.getAttribute('aria-pressed') === 'true' && document.querySelector('[data-miniapp-open=slides]')?.parentElement?.textContent.includes('Recently opened')", "both pin and successful recent restored");
    expect(await browser.evaluate<{ pinnedIds: string[]; recentIds: string[] }>(`JSON.parse(localStorage.getItem(${JSON.stringify(ownerStorageKey)}))`))
      .toMatchObject({ pinnedIds: ["health", "research"], recentIds: ["slides"] });
    await browser.evaluate("document.querySelector('#root').dispatchEvent(new CustomEvent('miniapp-owner', {detail:'stale-owner'}))");
    await browser.waitFor("[...document.querySelectorAll('[aria-label=\"Gideon miniapps\"] li')].find(card=>card.querySelector('strong')?.textContent==='Slides')?.querySelector('button:not(:disabled)')", "Slides control after stale owner change");
    const previousRoute = await browser.evaluate<string>("location.pathname + location.search");
    await browser.evaluate("[...document.querySelectorAll('[aria-label=\"Gideon miniapps\"] li')].find(card=>card.querySelector('strong')?.textContent==='Slides')?.querySelector('button')?.click()");
    await browser.waitFor("[...document.querySelectorAll('[aria-label=\"Gideon miniapps\"] li')].find(card=>card.querySelector('strong')?.textContent==='Slides')?.querySelector('[role=status]')?.textContent.includes('cannot open')", "native owner denial");
    expect(await browser.evaluate<string>("location.pathname + location.search")).toBe(previousRoute);
    expect(await browser.evaluate<boolean>("[...document.querySelectorAll('[aria-label=\"Gideon miniapps\"] li')].find(card=>card.querySelector('strong')?.textContent==='Slides')?.querySelector('button')?.disabled"))
      .toBe(true);

    await browser.evaluate("document.querySelector('#root').dispatchEvent(new CustomEvent('miniapp-owner', {detail:'studio-owner'}))");
    await browser.waitFor("[...document.querySelectorAll('[aria-label=\"Gideon miniapps\"] li')].find(card=>card.querySelector('strong')?.textContent==='Slides')?.querySelector('button:not(:disabled)')", "Slides control for original owner");
    expect((await fetch(`${controlOrigin}/hold`, { method: "POST" })).ok).toBe(true);
    await browser.evaluate("performance.clearResourceTimings(); document.querySelector('[data-miniapp-open=slides]')?.click()");
    await waitForNative("entered");
    await browser.evaluate("document.querySelector('button[aria-label=\"Back\"]')?.click()");
    await browser.waitFor("location.pathname === '/assistant/chat' && !document.querySelector('[aria-label=\"Gideon miniapps\"]')", "Apps unmounted during native check");
    await browser.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    expect((await fetch(`${controlOrigin}/release`, { method: "POST" })).ok).toBe(true);
    await waitForNative("finished");
    await browser.waitFor("performance.getEntriesByType('resource').some(entry => entry.name.includes('/api/artifacts?kind=pptx') && entry.responseEnd > 0)", "held native Slides response completed");
    expect(await browser.evaluate<string>("location.pathname")).toBe("/assistant/chat");
    expect(await browser.evaluate<boolean>("!!document.querySelector('[aria-label=\"Gideon miniapps\"]')")).toBe(false);
    expect(browser.diagnostics().filter(message => !message.includes("Download the React DevTools") && !message.includes("net::ERR_ABORTED"))).toEqual([]);
  }, 120000);
});
