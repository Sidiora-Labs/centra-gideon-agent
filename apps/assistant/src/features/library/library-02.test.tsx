import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer as createNetServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, describe, expect, it } from "vitest";

const root = resolve(process.cwd(), "../..");
const children: ChildProcessWithoutNullStreams[] = [];
const directories: string[] = [];
let vite: ViteDevServer | undefined;
let browser: WebSocket | undefined;
const browserErrors: string[] = [];

afterAll(async () => {
  browser?.close();
  for (const child of children) child.kill("SIGTERM");
  if (vite) await vite.close();
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

const knowledgeServer = String.raw`
import asyncio, json, os, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
from aiohttp import web
import gideon.core.config.loader as loader
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.interfaces.dashboard import session_store, token_auth
from gideon.interfaces.dashboard.handlers import auth
from gideon.interfaces.dashboard.handlers.knowledge import (
  list_items, get_item, library_home, list_collections, create_collection,
  get_collection_items, add_collection_items, remove_collection_item,
  set_item_read_state, set_item_favorited,
)
from gideon.security.auth import credentials

async def main(origin):
  with tempfile.TemporaryDirectory(prefix="gideon-library-native-") as directory:
    home = Path(directory)
    loader.config_dir = lambda: home
    credentials.config_dir = lambda: home
    session_store.config_dir = lambda: home
    (home / "config.json").write_text(json.dumps({"auth": {"login_enabled": True}}), encoding="utf-8")
    credentials.set_password("library-owner", "correct-horse-battery-staple")
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    store = KnowledgeStore(str(home / "knowledge.db"))
    created = store.create_typed_item(item_type="note", title="Native library record", content="A real record from the ephemeral Gideon knowledge store.", provider="native")
    native_id = "native item?one"
    store.db.execute("UPDATE items SET id = ? WHERE id = ?", (native_id, created))
    store.set_read_state(native_id, "reading")
    store.db.execute("UPDATE items SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (native_id,))
    second = store.create_typed_item(item_type="note", title="Second native record", content="The second real record from the ephemeral Gideon knowledge store.", provider="native")
    second_id = "native item?two"
    store.db.execute("UPDATE items SET id = ? WHERE id = ?", (second_id, second))
    store.set_favorited(second_id, True)
    for index in range(105):
      store.create_typed_item(item_type="note", title=f"Later native record {index:03d}", content="A real additional record used to exercise native pagination.", provider="native")
    async def delayed_get_item(request):
      if request.match_info["id"] == second_id: await asyncio.sleep(1)
      return await get_item(request)
    async def delayed_library_home(request):
      await asyncio.sleep(0.5)
      return await library_home(request)
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=10000)])
    app["port"] = 10000
    app["allowed_origins"] = {origin}
    app["state"] = SimpleNamespace(knowledge_store=store)
    app.router.add_get("/api/auth/status", auth.api_login_status)
    app.router.add_get("/api/auth/session", auth.api_auth_session)
    app.router.add_post("/api/auth/login", auth.api_auth_login)
    app.router.add_post("/api/auth/logout", auth.api_auth_logout)
    app.router.add_get("/api/knowledge/items", list_items)
    app.router.add_get("/api/knowledge/items/{id}", delayed_get_item)
    app.router.add_get("/api/knowledge/library-home", delayed_library_home)
    app.router.add_get("/api/knowledge/collections", list_collections)
    app.router.add_post("/api/knowledge/collections", create_collection)
    app.router.add_get("/api/knowledge/collections/{id}/items", get_collection_items)
    app.router.add_post("/api/knowledge/collections/{id}/items", add_collection_items)
    app.router.add_delete("/api/knowledge/collections/{id}/items/{item_id}", remove_collection_item)
    app.router.add_post("/api/knowledge/items/{id}/read-state", set_item_read_state)
    app.router.add_post("/api/knowledge/items/{id}/favorite", set_item_favorited)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    print(json.dumps({"api_port": site._server.sockets[0].getsockname()[1], "record_id": native_id, "second_record_id": second_id}), flush=True)
    try: await asyncio.Event().wait()
    finally:
      await runner.cleanup()
      store.db.close()

asyncio.run(main(sys.argv[1]))
`;

async function startApi(origin: string): Promise<{ url: string; recordId: string; secondRecordId: string }> {
  const gideonHome = await mkdtemp(join(tmpdir(), "gideon-library-home-"));
  directories.push(gideonHome);
  const child = spawn(process.env.GIDEON_TEST_PYTHON || "python3", ["-c", knowledgeServer, origin], {
    env: { ...process.env, GIDEON_HOME: gideonHome, PYTHONPATH: join(root, "runtime") },
  });
  children.push(child);
  const line = await new Promise<string>((done, fail) => {
    let output = "";
    let errors = "";
    const timeout = setTimeout(() => fail(new Error(`Native knowledge API start timed out: ${errors}`)), 20000);
    child.stdout.on("data", chunk => {
      output += String(chunk);
      if (output.includes("\n")) { clearTimeout(timeout); done(output.split("\n")[0]); }
    });
    child.stderr.on("data", chunk => { errors += String(chunk); });
    child.once("exit", code => { clearTimeout(timeout); fail(new Error(`Native knowledge API exited ${code}: ${errors}`)); });
  });
  const ready = JSON.parse(line) as { api_port: number; record_id: string; second_record_id: string };
  return { url: `http://127.0.0.1:${ready.api_port}`, recordId: ready.record_id, secondRecordId: ready.second_record_id };
}

async function startVite(port: number, api: string): Promise<void> {
  vite = await createServer({
    configFile: false,
    root: join(root, "apps/assistant"),
    resolve: { alias: [{ find: /^react-native$/, replacement: "react-native-web" }],
      extensions: [".web.tsx", ".web.ts", ".web.js", ".tsx", ".ts", ".js", ".jsx", ".json"] },
    esbuild: { jsx: "automatic" },
    optimizeDeps: { esbuildOptions: { resolveExtensions: [".web.tsx", ".web.ts", ".web.js", ".tsx", ".ts", ".js", ".jsx", ".json"] } },
    plugins: [{
      name: "assistant-library-browser",
      resolveId(id) { if (id === "/library-browser.tsx") return "\0library-browser"; },
      load(id) { if (id === "\0library-browser") return `
        import React from 'react';
        import { createRoot } from 'react-dom/client';
        import { LibraryHome } from '/src/features/library/LibraryHome.web.tsx';
        import { ownerScope, readOwnerSession, readLoginStatus, signInOwner, OwnerSignIn } from '/src/shared/auth.web.tsx';
        import { parseShellRoute } from '/src/shared/shell/shellRoutes.ts';
        function Root(){
          const [identity,setIdentity]=React.useState(null); const [status,setStatus]=React.useState(null); const [error,setError]=React.useState(''); const [route,setRoute]=React.useState(()=>parseShellRoute(location.href,location.origin)); const [scopeChanged,setScopeChanged]=React.useState(false);
          React.useEffect(()=>{readLoginStatus().then(setStatus);readOwnerSession().then(setIdentity).catch(()=>{});const pop=()=>setRoute(parseShellRoute(location.href,location.origin));addEventListener('popstate',pop);return()=>removeEventListener('popstate',pop)},[]);
          const navigate=next=>{const {serializeShellRoute}=requireShellRoutes;history.pushState(null,'',serializeShellRoute(next));setRoute(parseShellRoute(location.href,location.origin))};
          if(!identity)return React.createElement(OwnerSignIn,{status,error,onRetry:()=>readOwnerSession().then(setIdentity).catch(()=>{}),onSignIn:async(u,p,t)=>{try{await signInOwner(u,p,t);setIdentity(await readOwnerSession());}catch(e){setError(String(e))}}});
          const scopedIdentity=scopeChanged?{...identity,user:'library-owner-second-scope'}:identity;
          return React.createElement(React.Fragment,null,React.createElement('button',{type:'button',id:'switch-library-owner-scope',onClick:()=>setScopeChanged(current=>!current)},'Switch owner scope'),React.createElement(LibraryHome,{route,scope:ownerScope(location.origin,scopedIdentity),navigate}));
        }
        import { serializeShellRoute as _serializeShellRoute } from '/src/shared/shell/shellRoutes.ts';
        const requireShellRoutes={serializeShellRoute:_serializeShellRoute};
        createRoot(document.getElementById('root')).render(React.createElement(Root));
      `; },
      configureServer(server) {
        server.middlewares.use("/assistant", (_request, response) => {
          response.setHeader("Content-Type", "text/html; charset=utf-8");
          response.end('<!doctype html><html><body style="margin:0"><div id="root"></div><script type="module" src="/library-browser.tsx"></script></body></html>');
        });
      },
    }],
    server: { host: "127.0.0.1", port, strictPort: true, proxy: { "/api": api } },
  });
  await vite.listen();
}

async function startBrowser(): Promise<(method: string, params?: Record<string, unknown>) => Promise<any>> {
  const directory = await mkdtemp(join(tmpdir(), "gideon-library-browser-"));
  directories.push(directory);
  const port = await availablePort();
  const child = spawn(process.env.CHROMIUM_BIN || "chromium", ["--headless", "--no-sandbox", "--disable-gpu",
    "--disable-background-networking", `--remote-debugging-port=${port}`, `--user-data-dir=${directory}`, "about:blank"]);
  children.push(child);
  let targets: Array<{ type: string; webSocketDebuggerUrl: string }> | undefined;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); break; }
    catch { await new Promise(done => setTimeout(done, 100)); }
  }
  if (!targets) throw new Error("Chromium debugger unavailable");
  const target = targets.find(item => item.type === "page");
  if (!target) throw new Error("Chromium page unavailable");
  browser = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise<void>((done, fail) => {
    browser!.addEventListener("open", () => done(), { once: true });
    browser!.addEventListener("error", () => fail(new Error("Chromium connection failed")), { once: true });
  });
  let nextId = 0;
  const pending = new Map<number, { done: (value: any) => void; fail: (error: Error) => void }>();
  browser.addEventListener("message", event => {
    const message = JSON.parse(String(event.data)) as { id?: number; method?: string; params?: any; result?: any; error?: { message: string } };
    if (message.method === "Runtime.exceptionThrown") browserErrors.push(JSON.stringify(message.params?.exceptionDetails));
    if (message.id === undefined) return;
    const waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    if (message.error) waiter.fail(new Error(message.error.message));
    else waiter.done(message.result);
  });
  return (method, params = {}) => new Promise((done, fail) => {
    const id = ++nextId;
    pending.set(id, { done, fail });
    browser!.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(send: (method: string, params?: Record<string, unknown>) => Promise<any>, expression: string): Promise<any> {
  const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Browser evaluation failed");
  return result.result?.value;
}

async function waitFor(send: (method: string, params?: Record<string, unknown>) => Promise<any>, expression: string): Promise<void> {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (await evaluate(send, `Boolean(${expression})`)) return;
    await new Promise(done => setTimeout(done, 100));
  }
  const body = await evaluate(send, "document.body?.innerText || ''");
  throw new Error(`Timed out waiting for ${expression}: ${body}; ${browserErrors.join(" | ")}`);
}

describe("Library native curation", () => {
  it("searches, filters, creates collections, persists state and exposes native write rejection", async () => {
    const port = await availablePort();
    const origin = `http://127.0.0.1:${port}`;
    const api = await startApi(origin);
    await startVite(port, api.url);
    const send = await startBrowser();
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Page.navigate", { url: `${origin}/assistant/apps?v=1&view=workspace&placement=knowledge` });
    await waitFor(send, "document.querySelector('#gideon-password')");
    await evaluate(send, `(()=>{const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};set('gideon-username','library-owner');set('gideon-password','correct-horse-battery-staple');document.querySelector('form').requestSubmit();return true})()`);
    await waitFor(send, "document.body.innerText.includes('Recently added')");
    await evaluate(send, "Array.from(document.querySelectorAll('nav[aria-label] button')).find(button=>button.closest('nav')?.getAttribute('aria-label')==='Library sections'&&button.innerText==='Search')?.click()");
    await evaluate(send, "(()=>{const input=document.querySelector('input[type=search]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Native library record');input.dispatchEvent(new Event('input',{bubbles:true}));input.form.requestSubmit();return true})()");
    await waitFor(send, "document.body.innerText.includes('Native library record') && document.body.innerText.includes('matching item')");
    await evaluate(send, "Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Add favorite')?.click()");
    await waitFor(send, "Array.from(document.querySelectorAll('button')).some(button=>button.innerText==='Remove favorite')");
    await evaluate(send, "document.getElementById('switch-library-owner-scope')?.click()");
    await waitFor(send, "!document.body.innerText.includes('Native library record') && document.body.innerText.includes('Your Library')");
    await evaluate(send, "document.getElementById('switch-library-owner-scope')?.click()");
    await waitFor(send, "document.body.innerText.includes('Recently added')");
    await evaluate(send, "Array.from(document.querySelectorAll('nav[aria-label] button')).find(button=>button.closest('nav')?.getAttribute('aria-label')==='Library sections'&&button.innerText==='Search')?.click()");
    await evaluate(send, "(()=>{const input=document.querySelector('input[type=search]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Native library record');input.dispatchEvent(new Event('input',{bubbles:true}));input.form.requestSubmit();return true})()");
    await waitFor(send, "document.body.innerText.includes('Native library record') && document.body.innerText.includes('matching item')");
    const paginationState = await evaluate(send, "(async()=>{const first=await (await fetch('/api/knowledge/items?limit=100&page=1')).json();const second=await (await fetch('/api/knowledge/items?limit=100&page=2')).json();return {total:first.total,page1:first.items.length,page2:second.items.length,targetPage1:first.items.find(item=>item.id==='native item?one')?.favorited,targetPage2:second.items.find(item=>item.id==='native item?one')?.favorited,secondPageFavorites:second.items.filter(item=>item.favorited).map(item=>item.title)}})()");
    expect(paginationState.total).toBe(107);
    expect([paginationState.page1, paginationState.page2]).toEqual([100, 7]);
    expect(paginationState.targetPage1 === true || paginationState.targetPage2 === true).toBe(true);
    await evaluate(send, "(()=>{const select=Array.from(document.querySelectorAll('select')).find(value=>value.getAttribute('aria-label')?.startsWith('Reading progress'));Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,'read');select.dispatchEvent(new Event('change',{bubbles:true}));return true})()");
    await waitFor(send, "Array.from(document.querySelectorAll('select')).some(value=>value.value==='read')");
    await evaluate(send, "document.querySelector('section[aria-labelledby=library-search-title] form button[type=submit]')?.click()");
    await waitFor(send, "document.body.innerText.includes('Favorite')");
    await evaluate(send, "(()=>{const query=document.querySelector('input[type=search]');query.focus();return true})()");
    await waitFor(send, "document.activeElement===document.querySelector('input[type=search]')");
    await evaluate(send, "(()=>{const query=document.querySelector('input[type=search]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(query,'');query.dispatchEvent(new Event('input',{bubbles:true}));return true})()");
    await waitFor(send, "document.querySelector('input[type=search]').value==='' ");
    await evaluate(send, "(()=>{const favorite=Array.from(document.querySelectorAll('label')).find(label=>label.innerText.startsWith('Favorite'))?.querySelector('select');if(!favorite)return false;Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(favorite,'yes');favorite.dispatchEvent(new Event('change',{bubbles:true}));return true})()");
    await waitFor(send, "Array.from(document.querySelectorAll('label')).find(label=>label.innerText.startsWith('Favorite'))?.querySelector('select')?.value==='yes'");
    await evaluate(send, "document.querySelector('section[aria-labelledby=library-search-title] form button[type=submit]')?.click()");
    await waitFor(send, "document.body.innerText.includes('Native library record') && document.body.innerText.includes('matching item')");
    await evaluate(send, "Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Collections')?.click()");
    await waitFor(send, "document.body.innerText.includes('Create a collection')");
    await evaluate(send, "(()=>{const input=Array.from(document.querySelectorAll('input')).find(value=>value.maxLength===80);input.focus();return true})()");
    await send("Input.insertText", { text: "Research shelf" });
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, text: "\r", unmodifiedText: "\r" });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
    await waitFor(send, "document.body.innerText.includes('Manual collection created.')");
    await evaluate(send, "Array.from(document.querySelectorAll('nav[aria-label] button')).find(button=>button.closest('nav')?.getAttribute('aria-label')==='Library sections'&&button.innerText==='Search')?.click()");
    await waitFor(send, "document.body.innerText.includes('Search your knowledge')");
    await evaluate(send, "document.querySelector('section[aria-labelledby=library-search-title] form button[type=submit]')?.click()");
    await waitFor(send, "document.body.innerText.includes('Native library record') && document.body.innerText.includes('matching items')");
    await waitFor(send, "document.querySelector('select[aria-label=\"Manual collection\"]')");
    await evaluate(send, "(()=>{const select=document.querySelector('select[aria-label=\"Manual collection\"]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,select.options[1].value);select.dispatchEvent(new Event('change',{bubbles:true}));const row=Array.from(document.querySelectorAll('li')).find(item=>item.innerText.includes('Native library record'));Array.from(row?.querySelectorAll('button')||[]).find(button=>button.innerText==='Add to collection')?.click();return Boolean(row)})()");
    await waitFor(send, "document.body.innerText.includes('Added “Native library record” to Research shelf.')");
    await evaluate(send, "Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Collections')?.click()");
    await waitFor(send, "document.body.innerText.includes('Native library record')");
    await evaluate(send, "(()=>{const type=Array.from(document.querySelectorAll('label')).find(label=>label.innerText.startsWith('Collection type'))?.querySelector('select');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(type,'smart');type.dispatchEvent(new Event('change',{bubbles:true}));return true})()");
    await waitFor(send, "Array.from(document.querySelectorAll('input')).some(input=>input.required && input.maxLength!==80)");
    await evaluate(send, "(()=>{const query=Array.from(document.querySelectorAll('input')).find(input=>input.required && input.maxLength!==80);query.focus();return true})()");
    await send("Input.insertText", { text: "Native library record" });
    await evaluate(send, "(()=>{const name=Array.from(document.querySelectorAll('input')).find(input=>input.maxLength===80);name.focus();return true})()");
    await send("Input.insertText", { text: "Research smart" });
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, text: "\r", unmodifiedText: "\r" });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
    await waitFor(send, "document.body.innerText.includes('Smart collection created.') && document.body.innerText.includes('Native library record')");
    await send("Page.reload", { ignoreCache: true });
    await waitFor(send, "performance.getEntriesByType('navigation')[0]?.type === 'reload' && document.body.innerText.includes('Favorites')");
    await evaluate(send, "Array.from(document.querySelectorAll('nav[aria-label] button')).find(button=>button.closest('nav')?.getAttribute('aria-label')==='Library sections'&&button.innerText==='Search')?.click()");
    await evaluate(send, "(()=>{const input=document.querySelector('input[type=search]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Native library record');input.dispatchEvent(new Event('input',{bubbles:true}));input.form.requestSubmit();return true})()");
    await waitFor(send, "Array.from(document.querySelectorAll('button')).some(button=>button.innerText==='Remove favorite') && Array.from(document.querySelectorAll('select')).some(select=>select.getAttribute('aria-label')?.startsWith('Reading progress') && select.value==='read')");
    await evaluate(send, "(()=>{const input=document.querySelector('input[type=search]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'no-match-query-zzq');input.dispatchEvent(new Event('input',{bubbles:true}));input.form.requestSubmit();return true})()");
    await waitFor(send, "document.body.innerText.includes('No matching knowledge items.')");
    await evaluate(send, "Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Collections')?.click()");
    await waitFor(send, "Array.from(document.querySelectorAll('select')).some(select=>Array.from(select.options).some(option=>option.innerText.includes('Research shelf')))");
    await evaluate(send, "(()=>{const input=Array.from(document.querySelectorAll('input')).find(value=>value.maxLength===80);input.focus();return true})()");
    await send("Input.insertText", { text: "Research shelf" });
    await waitFor(send, "Array.from(document.querySelectorAll('input')).some(input=>input.maxLength===80 && input.value==='Research shelf')");
    await evaluate(send, "Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Create collection')?.click()");
    await waitFor(send, "document.querySelector('[role=alert]')?.innerText.includes('already exists')");
  }, 90000);
});
