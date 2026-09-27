import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer as createNetServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { startBrowserHarness, startViteEntryServer, type BrowserHarness } from "../../../test-support/browserHarness";
import { DESTINATIONS } from "./destinations";
import { MINIAPPS, miniappStateMessage } from "./miniapps";

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
import asyncio, json, os, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
from aiohttp import web

async def main(origin):
    with tempfile.TemporaryDirectory(prefix='gideon-miniapps-studio-') as directory:
        home = Path(directory)
        os.environ['GIDEON_HOME'] = str(home)
        from gideon.interfaces.dashboard import token_auth
        from gideon.security.auth import credentials
        from gideon.interfaces.dashboard.handlers import auth
        from gideon.workspace.artifacts import registry
        from gideon.workspace.artifacts.native import NativeArtifactProvider
        from gideon.workspace.artifacts.handlers import register_artifact_routes
        (home / 'config.json').write_text(json.dumps({'auth': {'login_enabled': True}, 'dashboard': {'document_editing': True}}), encoding='utf-8')
        credentials.set_password('studio-owner', 'correct-horse-battery-staple')
        token_auth.use_ephemeral_secret()
        registry.register_provider(NativeArtifactProvider(home / 'artifacts'))
        app = web.Application(middlewares=[token_auth.token_auth_middleware(port=10000)])
        app['port'] = 10000
        app['allowed_origins'] = {origin}
        app['state'] = SimpleNamespace(_restricted_keys=set(), _sessions={})
        app.router.add_post('/api/auth/login', auth.api_auth_login)
        app.router.add_get('/api/auth/session', auth.api_auth_session)
        register_artifact_routes(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        print(json.dumps({'api_port': site._server.sockets[0].getsockname()[1]}), flush=True)
        try: await asyncio.Event().wait()
        finally: await runner.cleanup()

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
    const startup = await new Promise<{ api_port: number }>((done, fail) => {
      let stdout = "";
      let stderr = "";
      const timer = setTimeout(() => fail(new Error(`Native Studio server did not start: ${stderr}`)), 20000);
      api.stdout.on("data", chunk => {
        stdout += String(chunk);
        if (stdout.includes("\n")) { clearTimeout(timer); done(JSON.parse(stdout.split("\n")[0]) as { api_port: number }); }
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
  const [route, setRoute] = React.useState(() => createShellRoute('apps', { view: 'workspace', placement: { id: 'apps' } }));
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
  const navigate = next => { setRoute(next); };
  const returnToApps = () => { setRoute(createShellRoute('apps', { view: 'workspace', placement: { id: 'apps' } })); };
  if (!identity) return <p role="status">Signing in to the native Studio account…</p>;
  if (route.destination === 'apps' && route.placement?.id === 'apps') return <AppsScreen route={route} scope={scope}
    navigate={navigate} onReturn={returnToApps} />;
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

    await browser.evaluate("[...document.querySelectorAll('[aria-label=\"Gideon miniapps\"] li')].find(card=>card.querySelector('strong')?.textContent==='Slides')?.querySelector('button')?.click()");
    await browser.waitFor("document.querySelector('[aria-label=\"New presentation name\"]')", "native Slides first-action workspace");
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
    await browser.evaluate("document.querySelector('#root').dispatchEvent(new CustomEvent('miniapp-owner', {detail:'stale-owner'}))");
    await browser.waitFor("[...document.querySelectorAll('[aria-label=\"Gideon miniapps\"] li')].find(card=>card.querySelector('strong')?.textContent==='Slides')?.querySelector('button:not(:disabled)')", "Slides control after stale owner change");
    const previousRoute = await browser.evaluate<string>("location.pathname + location.search");
    await browser.evaluate("[...document.querySelectorAll('[aria-label=\"Gideon miniapps\"] li')].find(card=>card.querySelector('strong')?.textContent==='Slides')?.querySelector('button')?.click()");
    await browser.waitFor("[...document.querySelectorAll('[aria-label=\"Gideon miniapps\"] li')].find(card=>card.querySelector('strong')?.textContent==='Slides')?.querySelector('[role=status]')?.textContent.includes('cannot open')", "native owner denial");
    expect(await browser.evaluate<string>("location.pathname + location.search")).toBe(previousRoute);
    expect(await browser.evaluate<boolean>("[...document.querySelectorAll('[aria-label=\"Gideon miniapps\"] li')].find(card=>card.querySelector('strong')?.textContent==='Slides')?.querySelector('button')?.disabled"))
      .toBe(true);
    expect(browser.diagnostics().filter(message => !message.includes("Download the React DevTools") && !message.includes("net::ERR_ABORTED"))).toEqual([]);
  }, 120000);
});
