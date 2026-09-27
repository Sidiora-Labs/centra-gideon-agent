import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer as createNetServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, describe, expect, it } from "vitest";
import { assistantConsoleReturnHref } from "../../../../console/src/app/shell/assistantRouteBridge";
import { consoleReturnHref } from "./entry.web";

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

async function startApi(origin: string): Promise<string> {
  const child = spawn(process.env.GIDEON_TEST_PYTHON || "python3",
    [join(root, "apps/assistant/test-support/conversation_server.py"), origin],
    { env: { ...process.env, PYTHONPATH: join(root, "runtime"), GIDEON_TEST_MODEL: "" } });
  children.push(child);
  const line = await new Promise<string>((done, fail) => {
    let output = "";
    let errors = "";
    const timeout = setTimeout(() => fail(new Error(`API start timed out: ${errors}`)), 15000);
    child.stdout.on("data", chunk => {
      output += String(chunk);
      if (output.includes("\n")) { clearTimeout(timeout); done(output.split("\n")[0]); }
    });
    child.stderr.on("data", chunk => { errors += String(chunk); });
    child.once("exit", code => { clearTimeout(timeout); fail(new Error(`API exited ${code}: ${errors}`)); });
  });
  return `http://127.0.0.1:${(JSON.parse(line) as { api_port: number }).api_port}`;
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
      name: "assistant-entry-browser",
      resolveId(id) { if (id === "/entry-browser.tsx") return "\0entry-browser"; },
      load(id) { if (id === "\0entry-browser") return `
        import React from 'react';
        import { createRoot } from 'react-dom/client';
        import App from '/App.tsx';
        createRoot(document.getElementById('root')).render(React.createElement(App));
      `; },
      configureServer(server) {
        server.middlewares.use("/assistant", (_request, response) => {
          response.setHeader("Content-Type", "text/html; charset=utf-8");
          response.end('<!doctype html><html><body><div id="root"></div><script type="module" src="/entry-browser.tsx"></script></body></html>');
        });
      },
    }],
    server: { host: "127.0.0.1", port, strictPort: true, proxy: { "/api": api } },
  });
  await vite.listen();
}

async function startBrowser(): Promise<(method: string, params?: Record<string, unknown>) => Promise<any>> {
  const directory = await mkdtemp(join(tmpdir(), "gideon-entry-browser-"));
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

async function waitFor(send: (method: string, params?: Record<string, unknown>) => Promise<any>, expression: string): Promise<any> {
  return evaluate(send, `(async()=>{for(let n=0;n<100;n++){const value=(${expression});if(value)return true;await new Promise(done=>setTimeout(done,100))}throw Error('Timed out: '+document.body.innerText)})()`)
    .catch(error => { throw new Error(`${error}: ${browserErrors.join(" | ")}`); });
}

describe("assistant web entry", () => {
  it("keeps assistant and console return links equivalent without leaking private query fields", () => {
    const context = { destination: "chat" as const, sessionId: "session/one", placement: {
      id: "console", query: { tab: "history", draft: "private", access_token: "private" },
    } };
    expect(consoleReturnHref(context)).toBe(assistantConsoleReturnHref(context));
    expect(consoleReturnHref(context)).toBe("/#/chat/session%2Fone?tab=history");
  });

  it("opens an owned session after sign-in, survives reload and Back, and recovers a missing session", async () => {
    const port = await availablePort();
    const origin = `http://127.0.0.1:${port}`;
    await startVite(port, await startApi(origin));
    const send = await startBrowser();
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Page.navigate", { url: `${origin}/assistant/chat?v=1` });
    await waitFor(send, `document.querySelector('#gideon-password')`);
    await evaluate(send, `(()=>{
      const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};
      set('gideon-username','conversation-owner');set('gideon-password','correct-horse-battery-staple');document.querySelector('form').requestSubmit();return true
    })()`);
    await waitFor(send, `document.body.innerText.includes('Your Gideon workspace')`);
    const created = await evaluate(send, `fetch('/api/chat/sessions',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'},body:'{}'}).then(r=>r.json())`);
    expect(typeof created.key).toBe("string");
    const sessionUrl = `${origin}/assistant/chat?v=1&view=detail&recordKind=session&recordId=${encodeURIComponent(created.key)}&from=apps&fromPlacement=console&fromq.tab=details`;
    await evaluate(send, `fetch('/api/auth/logout',{method:'POST',credentials:'same-origin',headers:{'X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'}}).then(()=>true)`);
    await send("Page.navigate", { url: sessionUrl });
    await waitFor(send, `document.querySelector('#gideon-password')`);
    expect(await evaluate(send, `location.href`)).toBe(sessionUrl);
    await evaluate(send, `(()=>{
      const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};
      set('gideon-username','conversation-owner');set('gideon-password','correct-horse-battery-staple');document.querySelector('form').requestSubmit();return true
    })()`);
    await waitFor(send, `document.body.innerText.includes('0 messages in this conversation.')`);
    expect(await evaluate(send, `location.href`)).toBe(sessionUrl);
    await send("Page.reload", { ignoreCache: true });
    await waitFor(send, `document.body.innerText.includes('0 messages in this conversation.')`);
    await evaluate(send, `document.querySelector('[aria-label="Ideas"]')?.click()`);
    await waitFor(send, `location.pathname === '/assistant/ideas' && document.body.innerText.includes('Open Ideas in Gideon console')`);
    await evaluate(send, `history.back()`);
    await waitFor(send, `location.href === ${JSON.stringify(sessionUrl)} && document.body.innerText.includes('0 messages in this conversation.')`);
    await evaluate(send, `Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Return to previous workspace')?.click()`);
    await waitFor(send, `location.pathname === '/' && location.hash === '#/apps?tab=details'`);
    await send("Page.navigate", { url: `${origin}/assistant/chat?v=1&view=detail&recordKind=session&recordId=absent-session` });
    await waitFor(send, `document.body.innerText.includes('The requested item may have been removed.')`);
    await evaluate(send, `Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Go to Chat')?.click()`);
    await waitFor(send, `location.pathname === '/assistant/chat' && document.body.innerText.includes('Your Gideon workspace')`);
    await send("Page.navigate", { url: `${origin}/assistant/apps?v=1&view=detail&recordKind=app&recordId=app-1` });
    await waitFor(send, `document.body.innerText.includes('This item cannot be checked') && document.body.innerText.includes('Retry destination')`);
  }, 90000);
});
