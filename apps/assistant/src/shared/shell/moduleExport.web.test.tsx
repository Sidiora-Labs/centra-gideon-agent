import { createServer as createNetServer } from "node:net";
import { createServer, request as httpRequest, type Server as HttpServer } from "node:http";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { extname, join, resolve, sep } from "node:path";
import { tmpdir } from "node:os";
import { afterAll, describe, expect, it } from "vitest";
import { startBrowserHarness, type BrowserHarness } from "../../../test-support/browserHarness";
import { startNativeServer, type NativeServer } from "../../../test-support/nativeServer";

const assistant = process.cwd();
const root = assistant.replace(/\/apps\/assistant$/, "");
let webServer: HttpServer | undefined;
let browser: BrowserHarness | undefined;
let native: NativeServer | undefined;

function contentType(file: string): string {
  return ({
    ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".map": "application/json; charset=utf-8", ".svg": "image/svg+xml",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".wasm": "application/wasm",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
  } as Record<string, string>)[extname(file)] ?? "application/octet-stream";
}

async function serveExport(exportDirectory: string, apiOrigin: string, port: number): Promise<{ server: HttpServer; origin: string }> {
  const api = new URL(apiOrigin);
  const server = createServer(async (request, response) => {
    const pathname = new URL(request.url ?? "/", "http://gideon.test").pathname;
    if (pathname === "/api" || pathname.startsWith("/api/")) {
      const proxy = httpRequest({ hostname: api.hostname, port: api.port, path: request.url,
        method: request.method, headers: { ...request.headers, host: api.host } }, upstream => {
        response.writeHead(upstream.statusCode ?? 502, upstream.headers);
        upstream.pipe(response);
      });
      proxy.on("error", error => { response.writeHead(502); response.end(String(error)); });
      request.pipe(proxy);
      return;
    }

    const relative = pathname.replace(/^\/assistant(?:\/|$)/, "").replace(/^\/+/, "");
    const requested = !relative || relative.endsWith("/") ? `${relative}index.html` : relative;
    let candidate = resolve(exportDirectory, requested);
    if (candidate !== exportDirectory && !candidate.startsWith(`${exportDirectory}${sep}`)) {
      response.writeHead(403); response.end("Forbidden"); return;
    }
    try {
      if (!(await stat(candidate)).isFile()) throw new Error("not a file");
    } catch {
      candidate = resolve(exportDirectory, "index.html");
    }
    try {
      response.setHeader("Content-Type", contentType(candidate));
      response.end(await readFile(candidate));
    } catch {
      response.writeHead(404); response.end("Not found");
    }
  });
  await new Promise<void>((resolveListen, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolveListen);
  });
  return { server, origin: `http://127.0.0.1:${port}` };
}

async function reservePort(): Promise<number> {
  const probe = createNetServer();
  await new Promise<void>(resolve => probe.listen(0, "127.0.0.1", resolve));
  const address = probe.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise<void>(resolve => probe.close(() => resolve()));
  return port;
}

afterAll(async () => {
  await browser?.close();
  await native?.stop();
  if (webServer) await new Promise<void>(resolveClose => webServer?.close(() => resolveClose()));
});

describe("trusted modules in the Expo web export", () => {
  it("opens native TaskDetail and ContentSurface Monaco in one root and returns safely", async () => {
    const port = await reservePort();
    const origin = `http://127.0.0.1:${port}`;
    native = await startNativeServer({
      script: `${root}/apps/assistant/test-support/module_server.py`,
      origin,
      repositoryRoot: root,
    });
    const served = await serveExport(`${assistant}/dist/web`, native.apiOrigin, port);
    webServer = served.server;
    browser = await startBrowserHarness({ windowSize: { width: 1280, height: 900 } });
    await browser.command("Page.addScriptToEvaluateOnNewDocument", { source: `
      window.__gideonWorkerUrls = [];
      const OriginalWorker = window.Worker;
      window.Worker = new Proxy(OriginalWorker, { construct(target, args) {
        window.__gideonWorkerUrls.push(new URL(args[0], location.href).href);
        return Reflect.construct(target, args);
      }});
    ` });
    await browser.navigate(`${served.origin}/assistant/`);
    await browser.waitFor("document.querySelector('#gideon-password')", "real owner sign-in form");
    await browser.evaluate(`(()=>{
      const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};
      set('gideon-username','module-owner');set('gideon-password','correct-horse-battery-staple');document.querySelector('form').requestSubmit();return true
    })()`);
    await browser.waitFor(`document.querySelector('[data-gideon-assistant]')?.textContent.includes('Signed in as module-owner')`, "authenticated owner shell");

    const evidenceDirectory = process.env.GIDEON_TEST_EVIDENCE_DIR ?? join(tmpdir(), "gideon-assistant-shell-07-monaco-runtime");
    await mkdir(evidenceDirectory, { recursive: true });
    const shellScreenshot = await browser.command("Page.captureScreenshot", { format: "png" });
    await writeFile(join(evidenceDirectory, "authenticated-shell.png"), Buffer.from(String(shellScreenshot.data), "base64"));

    try {
    const taskId = String(native.startup.task_id);
    await browser.navigate(`${served.origin}/assistant/activity?v=1&view=detail&placement=tasks&recordKind=task&recordId=${encodeURIComponent(taskId)}&from=chat`);
    await browser.waitFor(`document.querySelector('[data-gideon-module="tasks"]')?.innerText.includes('Native task detail fixture')`, "native TaskDetail inside trusted module", 60000);
    const taskSurface = await browser.evaluate<{ rootCount: number; frameCount: number; iframeCount: number; detail: boolean }>(`({
      rootCount: document.querySelectorAll('#root').length,
      frameCount: document.querySelectorAll('main.gideon-workspace-frame').length,
      iframeCount: document.querySelectorAll('iframe').length,
      detail: document.querySelector('[data-gideon-module="tasks"]')?.innerText.includes('Native task detail fixture') || false
    })`);
    expect(taskSurface).toEqual({ rootCount: 1, frameCount: 1, iframeCount: 0, detail: true });

    const artifactSlug = String(native.startup.artifact_slug);
    await browser.navigate(`${served.origin}/assistant/apps?v=1&view=workspace&placement=artifacts%2Feditor&recordKind=artifact&recordId=${encodeURIComponent(artifactSlug)}&from=chat`);
    await browser.waitFor("document.querySelector('[data-gideon-module=\"artifacts/editor\"] button')", "native artifact editor actions");
    await browser.evaluate(`([...document.querySelectorAll('button')].find(button=>button.textContent?.trim()==='Edit'))?.click()`);
    await browser.waitFor("document.querySelector('.monaco-editor textarea.inputarea')", "initialized Monaco editor", 60000);
    await browser.waitFor("window.__gideonWorkerUrls?.length > 0", "local Monaco worker startup", 60000);
    const editorSurface = await browser.evaluate<{ rootCount: number; frameCount: number; iframeCount: number; geometry: boolean; focusable: boolean; workers: string[]; resources: string[]; htmlMode: string | null; storedMode: string | null }>(`(()=>{
      const module=document.querySelector('[data-gideon-module="artifacts/editor"]');
      const rect=module?.getBoundingClientRect();
      const input=document.querySelector('.monaco-editor textarea.inputarea');
      input?.focus();
      return {
        rootCount:document.querySelectorAll('#root').length,
        frameCount:document.querySelectorAll('main.gideon-workspace-frame').length,
        iframeCount:document.querySelectorAll('iframe').length,
        geometry:!!rect&&rect.width>600&&rect.height>300&&getComputedStyle(module).display==='flex',
        focusable:!!input&&document.activeElement===input,
        workers:[...window.__gideonWorkerUrls],
        resources:performance.getEntriesByType('resource').map(entry=>entry.name),
        htmlMode:document.documentElement.dataset.mode||null,
        storedMode:localStorage.getItem('mode')
      }
    })()`);
    expect(editorSurface.rootCount).toBe(1);
    expect(editorSurface.frameCount).toBe(1);
    expect(editorSurface.iframeCount).toBe(0);
    expect(editorSurface.geometry).toBe(true);
    expect(editorSurface.focusable).toBe(true);
    expect(editorSurface.workers.length).toBeGreaterThan(0);
    expect(editorSurface.workers.every(url => new URL(url).origin === origin && new URL(url).pathname.startsWith("/assistant/assets/workers/"))).toBe(true);
    expect(editorSurface.resources.some(url => url.includes("/assistant/assets/gideon-console.css"))).toBe(true);
    expect(editorSurface.resources.some(url => /monaco/i.test(url) && /cdn|jsdelivr/i.test(url))).toBe(false);
    expect(editorSurface.htmlMode).toBe(null);
    expect(editorSurface.storedMode).toBe(null);
    const exportAssets = await browser.evaluate<Array<{ url: string; status: number; contentType: string }>>(`(async()=>{
      const script=[...document.scripts].find(item=>item.src.includes('/_expo/static/js/web/'))?.src;
      const urls=[script,'/assistant/assets/gideon-console.css','/assistant/assets/fonts/inter.woff2',window.__gideonWorkerUrls[0]].filter(Boolean);
      return Promise.all(urls.map(async url=>{const response=await fetch(url);return {url,status:response.status,contentType:response.headers.get('content-type')||''}}));
    })()`);
    expect(exportAssets).toHaveLength(4);
    expect(exportAssets.every(asset => asset.status === 200)).toBe(true);
    expect(exportAssets.find(asset => asset.url.includes("/assets/gideon-console.css"))?.contentType).toContain("text/css");
    expect(exportAssets.find(asset => asset.url.includes("/_expo/static/js/web/"))?.contentType).toMatch(/javascript|ecmascript/);
    expect(exportAssets.find(asset => asset.url.includes("/assets/workers/"))?.contentType).toMatch(/javascript|ecmascript/);

    await browser.command("Input.dispatchKeyEvent", { type: "keyDown", key: "Control", code: "ControlLeft", modifiers: 2 });
    await browser.command("Input.dispatchKeyEvent", { type: "keyDown", key: "a", code: "KeyA", modifiers: 2 });
    await browser.command("Input.dispatchKeyEvent", { type: "keyUp", key: "a", code: "KeyA", modifiers: 2 });
    await browser.command("Input.dispatchKeyEvent", { type: "keyUp", key: "Control", code: "ControlLeft" });
    await browser.command("Input.insertText", { text: "Keyboard editor verification" });
    await browser.waitFor("document.querySelector('.monaco-editor .view-lines')?.innerText.includes('Keyboard editor verification')", "keyboard edits reflected in Monaco model");
    await browser.evaluate(`document.querySelector('[aria-label="Return to previous workspace"]')?.click()`);
    await browser.waitFor("location.pathname === '/assistant/chat' && !document.querySelector('[data-gideon-module]')", "typed return to assistant chat");
    } catch (error) {
      if (browser) {
        const failureScreenshot = await browser.command("Page.captureScreenshot", { format: "png" }).catch(() => undefined);
        if (failureScreenshot?.data) await writeFile(join(evidenceDirectory, "failure.png"), Buffer.from(String(failureScreenshot.data), "base64"));
      }
      throw error;
    }
  }, 180000);
});
