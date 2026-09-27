import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { cp, mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { createServer, request as httpRequest, type Server as HttpServer } from "node:http";
import { createServer as createNetServer, type Server as NetServer } from "node:net";
import { tmpdir } from "node:os";
import { extname, join, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";
import { test } from "node:test";

type BrowserHarness = {
  command: (method: string, params?: Record<string, unknown>) => Promise<Record<string, unknown>>;
  evaluate: <T = unknown>(expression: string) => Promise<T>;
  navigate: (url: string) => Promise<void>;
  waitFor: (expression: string, label?: string, timeoutMs?: number) => Promise<void>;
  diagnostics: () => readonly string[];
  close: () => Promise<void>;
};
type NativeServer = {
  apiOrigin: string;
  controlOrigin: string;
  startup: Record<string, unknown>;
  stop: () => Promise<void>;
};

const runFile = promisify(execFile);
const repository = resolve(process.cwd(), "../..");
const assistant = join(repository, "apps/assistant");
const password = "correct-horse-battery-staple";

async function listen(server: HttpServer | NetServer, port = 0) {
  await new Promise<void>(resolveListen => server.listen({ port, host: "127.0.0.1" }, resolveListen));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  return address.port;
}

function contentType(file: string) {
  return ({
    ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".woff": "font/woff",
    ".woff2": "font/woff2", ".ttf": "font/ttf", ".wasm": "application/wasm",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
  } as Record<string, string>)[extname(file)] ?? "application/octet-stream";
}

async function serveExport(exportDirectory: string, serviceWorkerFile: string, apiOrigin: string, port = 0) {
  const api = new URL(apiOrigin);
  const server = createServer(async (request, response) => {
    const pathname = new URL(request.url ?? "/", "http://gideon.test").pathname;
    if (pathname === "/sw.js") {
      response.setHeader("Content-Type", "text/javascript; charset=utf-8");
      response.setHeader("Service-Worker-Allowed", "/");
      response.end(await readFile(serviceWorkerFile));
      return;
    }
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

    let relative = pathname.replace(/^\/assistant(?:\/|$)/, "").replace(/^\/+/, "");
    if (!relative || relative.endsWith("/")) relative += "index.html";
    let candidate = resolve(exportDirectory, relative);
    if (candidate !== exportDirectory && !candidate.startsWith(`${exportDirectory}${sep}`)) {
      response.writeHead(403); response.end("Forbidden"); return;
    }
    try {
      const info = await stat(candidate);
      if (!info.isFile()) throw new Error("not a file");
    } catch {
      candidate = join(exportDirectory, "index.html");
    }
    try {
      response.setHeader("Content-Type", contentType(candidate));
      response.end(await readFile(candidate));
    } catch {
      response.writeHead(404); response.end("Not found");
    }
  });

  server.on("upgrade", (request, client, head) => {
    const proxy = httpRequest({ hostname: api.hostname, port: api.port, path: request.url,
      method: request.method, headers: { ...request.headers, host: api.host } });
    proxy.on("upgrade", (response, upstream, upstreamHead) => {
      const status = `HTTP/1.1 ${response.statusCode ?? 101} ${response.statusMessage ?? "Switching Protocols"}\r\n`;
      const headers = Object.entries(response.headers).flatMap(([key, value]) =>
        value === undefined ? [] : (Array.isArray(value) ? value : [value]).map(item => `${key}: ${item}\r\n`)).join("");
      client.write(status + headers + "\r\n");
      if (upstreamHead.length) client.write(upstreamHead);
      if (head.length) upstream.write(head);
      upstream.pipe(client);
      client.pipe(upstream);
    });
    proxy.on("response", response => {
      client.write(`HTTP/1.1 ${response.statusCode ?? 502} ${response.statusMessage ?? "Bad Gateway"}\r\n\r\n`);
      response.pipe(client);
    });
    proxy.on("error", () => client.destroy());
    proxy.end();
  });

  const listeningPort = await listen(server, port);
  return { server, origin: `http://127.0.0.1:${listeningPort}` };
}

async function buildRootServiceWorker(temporary: string, exportDirectory: string) {
  const stagedApps = join(temporary, "worker-build/apps");
  const stagedConsole = join(stagedApps, "console");
  const stagedAssistant = join(stagedApps, "assistant");
  await Promise.all([
    cp(join(repository, "apps/console/src/app"), join(stagedConsole, "src/app"), { recursive: true }),
    cp(join(repository, "apps/console/public"), join(stagedConsole, "public"), { recursive: true }),
    cp(join(assistant, "src"), join(stagedAssistant, "src"), { recursive: true }),
    cp(join(assistant, "tooling"), join(stagedAssistant, "tooling"), { recursive: true }),
    cp(exportDirectory, join(stagedAssistant, "dist/web"), { recursive: true }),
    (async () => {
      const source = join(assistant, "public");
      try { await stat(source); } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
        throw error;
      }
      await cp(source, join(stagedAssistant, "public"), { recursive: true });
    })(),
  ]);
  await mkdir(join(stagedConsole, "dist"), { recursive: true });
  await mkdir(join(stagedAssistant, "dist"), { recursive: true });
  for (const file of ["app.json", "metro.config.cjs", "package.json", "package-lock.json"]) {
    await cp(join(assistant, file), join(stagedAssistant, file));
  }
  const builderPath = join(repository, "apps/console/tooling/buildServiceWorker.mjs");
  const builderSource = (await readFile(builderPath, "utf8")).replace(
    "import esbuild from 'esbuild'",
    `import esbuild from ${JSON.stringify(pathToFileURL(join(assistant, "node_modules/esbuild/lib/main.js")).href)}`,
  );
  assert.notEqual(builderSource, await readFile(builderPath, "utf8"), "the official worker builder dependency resolves from the assistant package");
  const builder = await import(`data:text/javascript;base64,${Buffer.from(builderSource).toString("base64")}`) as {
    buildServiceWorker: (consoleRoot: string) => Promise<{ version: string; path: string }>;
  };
  return builder.buildServiceWorker(stagedConsole);
}

async function startBrowser(directory: string): Promise<BrowserHarness> {
  const { startBrowserHarness } = await import(pathToFileURL(join(assistant, "test-support/browserHarness.ts")).href) as {
    startBrowserHarness: (options: { profileDirectory: string; windowSize: { width: number; height: number } }) => Promise<BrowserHarness>;
  };
  return startBrowserHarness({ profileDirectory: directory, windowSize: { width: 1440, height: 960 } });
}

async function signIn(browser: BrowserHarness, username: string) {
  await browser.waitFor("document.querySelector('#gideon-password')", "sign-in form");
  await browser.evaluate(`(()=>{const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};set('gideon-username',${JSON.stringify(username)});set('gideon-password',${JSON.stringify(password)});document.querySelector('form').requestSubmit();return true})()`);
  await browser.waitFor(`document.querySelector('[data-gideon-assistant]')?.textContent.includes(${JSON.stringify(`Signed in as ${username}`)})`, `signed-in identity ${username}`);
  const typography = await browser.evaluate<{ heading: string; identity: string }>(`(()=>{const root=document.querySelector('[data-gideon-assistant]');const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let identity=root;while(walker.nextNode()){if(walker.currentNode.textContent?.includes('Signed in as')){identity=walker.currentNode.parentElement??root;break}}return {heading:getComputedStyle(root.querySelector('h1')).fontFamily,identity:getComputedStyle(identity).fontFamily}})()`);
  const nativeSans = /(system-ui|-apple-system|BlinkMacSystemFont|Segoe UI|Roboto|Arial|sans-serif)/i;
  assert.match(typography.heading, nativeSans);
  assert.match(typography.identity, nativeSans);
}

function routeHref(origin: string, destination: string, options: {
  view?: string;
  record?: { kind: string; id: string };
  placement?: string;
  returnTo?: {
    destination: string;
    sessionId?: string;
    selectionId?: string;
    scrollY?: number;
    placement?: { id: string; query?: Record<string, string> };
  };
} = {}) {
  const query = new URLSearchParams({ v: "1", view: options.view ?? "list" });
  if (options.record) {
    query.set("recordKind", options.record.kind);
    query.set("recordId", options.record.id);
  }
  if (options.placement) query.set("placement", options.placement);
  if (options.returnTo) {
    query.set("from", options.returnTo.destination);
    if (options.returnTo.placement) {
      query.set("fromPlacement", options.returnTo.placement.id);
      for (const [key, value] of Object.entries(options.returnTo.placement.query ?? {})) query.set(`from.q.${key}`, value);
    }
    if (options.returnTo.sessionId) query.set("fromSession", options.returnTo.sessionId);
    if (options.returnTo.selectionId) query.set("fromSelection", options.returnTo.selectionId);
    if (options.returnTo.scrollY !== undefined) query.set("fromScroll", String(options.returnTo.scrollY));
  }
  return `${origin}/assistant/${destination}?${query}`;
}

test("real Expo assistant shell preserves identity, route, trusted workspaces and conversation return", { timeout: 360000 }, async t => {
  const temporary = await mkdtemp(join(tmpdir(), "gideon-shell-e2e-"));
  const visualEvidence = await mkdtemp(join(tmpdir(), "gideon-shell-08-visual-"));
  const exportDirectory = join(temporary, "web");
  let webServer: ReturnType<typeof createServer> | undefined;
  let browser: BrowserHarness | undefined;
  let fixture: NativeServer | undefined;

  t.after(async () => {
    await browser?.close();
    await fixture?.stop();
    const server = webServer;
    if (server) await new Promise<void>(resolveClose => server.close(() => resolveClose()));
    await rm(temporary, { recursive: true, force: true });
  });

  const webProbe = createServer();
  const webPort = await listen(webProbe);
  await new Promise(resolveClose => webProbe.close(resolveClose));
  const expectedOrigin = `http://127.0.0.1:${webPort}`;
  const { startNativeServer } = await import(pathToFileURL(join(assistant, "test-support/nativeServer.ts")).href) as {
    startNativeServer: (options: { script: string; origin: string; repositoryRoot: string }) => Promise<NativeServer>;
  };
  fixture = await startNativeServer({
    script: join(assistant, "test-support/module_server.py"), origin: expectedOrigin, repositoryRoot: repository,
  });
  const fixtureInfo = fixture.startup;
  const username = String(fixtureInfo.username ?? "");
  assert.ok(username, "native fixture returns an account username");
  const fixtureState = await (await fetch(`${fixture.controlOrigin}/state`)).json();
  assert.equal(fixtureState.owner, username);
  assert.ok(fixtureState.task_id);
  assert.ok(fixtureState.artifact_slug);

  const exportResult = await runFile(process.execPath, [
    join(assistant, "node_modules/expo/bin/cli"), "export", "--platform", "web", "--output-dir", exportDirectory,
  ], { cwd: assistant, timeout: 240000, maxBuffer: 16 * 1024 * 1024 });
  assert.match(exportResult.stdout, /Exported|Web|export/i);
  await runFile(process.execPath, ["tooling/buildTrustedWebAssets.mjs", "--out-dir", join(exportDirectory, "assets")], {
    cwd: assistant, timeout: 180000, maxBuffer: 8 * 1024 * 1024,
  });
  assert.ok((await stat(join(exportDirectory, "index.html"))).size > 0);
  assert.ok((await stat(join(exportDirectory, "assets/gideon-console.css"))).size > 0);

  const worker = await buildRootServiceWorker(temporary, exportDirectory);
  assert.ok((await stat(worker.path)).size > 0);
  const served = await serveExport(exportDirectory, worker.path, fixture.apiOrigin, webPort);
  webServer = served.server;
  const workerResponse = await fetch(`${served.origin}/sw.js`);
  assert.equal(workerResponse.status, 200);
  assert.match(workerResponse.headers.get("content-type") ?? "", /javascript/);
  assert.match(await workerResponse.text(), new RegExp(`gideon-shell-${worker.version}`));
  browser = await startBrowser(temporary);

  const sessionBootstrap = `${served.origin}/assistant/chat?v=1`;
  await browser.command("Page.navigate", { url: sessionBootstrap });
  await signIn(browser, username);
  await browser.waitFor(`navigator.serviceWorker?.getRegistration('/').then(registration=>registration?.scope===location.origin+'/')`, "assistant root worker registration");
  await browser.command("Page.reload", { ignoreCache: true });
  await browser.waitFor(`document.querySelector('[data-gideon-assistant]')?.textContent.includes(${JSON.stringify(`Signed in as ${username}`)})`, "authenticated reload under service worker");
  await browser.waitFor(`navigator.serviceWorker?.controller?.scriptURL===location.origin+'/sw.js'`, "assistant service worker control");
  const workerRegistration = await browser.evaluate<{ scope: string; scriptURL: string }>(`navigator.serviceWorker.getRegistration('/').then(registration=>({scope:registration?.scope??'',scriptURL:registration?.active?.scriptURL??''}))`);
  assert.equal(workerRegistration.scope, `${served.origin}/`);
  assert.equal(workerRegistration.scriptURL, `${served.origin}/sw.js`);
  const session = await browser.evaluate<{ key: string }>(`fetch('/api/chat/sessions',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'},body:'{}'}).then(async r=>{if(!r.ok)throw Error('session creation failed: '+r.status);return r.json()})`);
  assert.ok(session.key);
  const sessionHref = routeHref(served.origin, "chat", {
    view: "detail", record: { kind: "chat_session", id: session.key },
  });
  await browser.evaluate(`document.querySelector('[aria-label="Open Gideon menu"]')?.click()`);
  await browser.waitFor(`document.querySelector('[data-gideon-assistant-overlay]')`, "workspace menu");
  await browser.evaluate(`Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Sign out')?.click()`);
  await browser.waitFor(`document.querySelector('#gideon-password')`, "signed-out state");
  await browser.command("Page.navigate", { url: sessionHref });
  await signIn(browser, username);
  await browser.waitFor("document.getElementById('gideon-message-composer')", "deep-linked conversation composer");
  assert.equal(await browser.evaluate("location.href"), sessionHref);
  assert.equal(await browser.evaluate(`document.querySelectorAll('#root').length`), 1);
  const returnMessage = "Shell return context marker. ".repeat(100);
  await browser.evaluate(`(()=>{const input=document.getElementById('gideon-message-composer');const setter=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input),'value').set;setter.call(input,${JSON.stringify(returnMessage)});input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('[aria-label="Send message"]')?.click();return true})()`);
  await browser.waitFor(`Array.from(document.querySelectorAll('[id^="gideon-message-"]')).some(message=>message.textContent.includes(${JSON.stringify(returnMessage.slice(0, 30))}))`, "persisted native user message for return context");
  const selectionId = await browser.evaluate<string>(`Array.from(document.querySelectorAll('[id^="gideon-message-"]')).find(message=>message.textContent.includes(${JSON.stringify(returnMessage.slice(0, 30))}) )?.id.replace(/^gideon-message-/,'')||''`);
  assert.ok(selectionId, "the native conversation rendered a stable message ID for selection");
  await browser.waitFor(`(()=>{const list=document.getElementById('gideon-chat-scroll');return !!list&&list.scrollHeight>list.clientHeight+320})()`, "conversation content tall enough to restore its scroll position");
  const scrollBeforeWorkspace = await browser.evaluate<number>(`(()=>{const list=document.getElementById('gideon-chat-scroll');list.scrollTop=320;list.dispatchEvent(new Event('scroll',{bubbles:true}));return list.scrollTop})()`);
  assert.ok(scrollBeforeWorkspace >= 300, `source conversation scroll was set before workspace handoff: ${scrollBeforeWorkspace}`);
  const apiHeaders = JSON.stringify({ "X-Gideon-API-Version": "1", "X-Session-Key": "dashboard:ui" });
  const taskRecord = await browser.evaluate<{ title: string }>(`fetch('/api/tasks/'+encodeURIComponent(${JSON.stringify(String(fixtureState.task_id))}),{credentials:'same-origin',headers:${apiHeaders}}).then(async response=>{if(!response.ok)throw Error('task fixture read failed: '+response.status);return response.json()})`);
  const artifactRecord = await browser.evaluate<{ name: string; content: string; kind: string }>(`fetch('/api/artifacts/'+encodeURIComponent(${JSON.stringify(String(fixtureState.artifact_slug))}),{credentials:'same-origin',headers:${apiHeaders}}).then(async response=>{if(!response.ok)throw Error('artifact fixture read failed: '+response.status);return response.json()})`);
  assert.ok(taskRecord.title);
  assert.ok(artifactRecord.name);

  const destinations = ["Chat", "Activity", "Ideas", "Goals", "Apps"];
  for (const label of destinations) {
    await browser.evaluate(`(()=>{const tab=document.querySelector('[role="tab"][aria-label=${JSON.stringify(label)}]');if(!tab)throw Error('missing destination ${label}');tab.click();return true})()`);
    await browser.waitFor(`document.querySelector('[role="tab"][aria-label=${JSON.stringify(label)}][aria-selected="true"]')`, `${label} destination selection`);
    const current: string = String(await browser.evaluate("location.pathname"));
    assert.equal(current, `/assistant/${label.toLowerCase()}`);
  }
  await browser.command("Page.navigate", { url: sessionHref });
  await browser.waitFor("document.getElementById('gideon-message-composer')", "owned chat session after reload");
  await browser.evaluate(`document.querySelector('[role="tab"][aria-label="Ideas"]')?.click()`);
  await browser.waitFor(`location.pathname==='/assistant/ideas'`, "Ideas route");
  await browser.command("Page.reload", { ignoreCache: true });
  await browser.waitFor(`location.pathname==='/assistant/ideas'&&document.querySelector('[role="tab"][aria-label="Ideas"][aria-selected="true"]')`, "Ideas reload selection");
  await browser.evaluate("history.back()");
  await browser.waitFor(`location.href===${JSON.stringify(sessionHref)}&&document.getElementById('gideon-message-composer')`, "history return to same chat");

  for (const viewport of [{ width: 1280, height: 900, mobile: false }, { width: 768, height: 1024, mobile: true }, { width: 360, height: 740, mobile: true }]) {
    await browser.command("Emulation.setDeviceMetricsOverride", { ...viewport, deviceScaleFactor: 1 });
    const geometry: { width: number; scroll: number; count: number; visible: number } = await browser.evaluate(`(()=>{const tabs=[...document.querySelectorAll('[role="tab"]')];return {width:innerWidth,scroll:document.documentElement.scrollWidth,count:tabs.length,visible:tabs.filter(tab=>{const r=tab.getBoundingClientRect();return r.width>0&&r.left>=0&&r.right<=innerWidth+1}).length}})()` as string) as { width: number; scroll: number; count: number; visible: number };
    assert.equal(geometry.count, 5);
    assert.equal(geometry.visible, 5);
    assert.ok(geometry.scroll <= geometry.width + 1, JSON.stringify(geometry));
  }

  await browser.command("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  await browser.evaluate(`(()=>{const menu=document.querySelector('[aria-label="Open Gideon menu"]');menu.focus();menu.click();return true})()`);
  await browser.waitFor(`document.querySelector('[data-gideon-assistant-overlay]')`, "theme overlay");
  await browser.evaluate(`document.querySelector('[aria-label="Dark theme"]')?.click()`);
  await browser.waitFor(`document.querySelector('[data-gideon-assistant][data-theme="dark"]')`, "dark theme");
  await browser.command("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await browser.command("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await browser.waitFor(`!document.querySelector('[data-gideon-assistant-overlay]')&&document.activeElement?.getAttribute('aria-label')==='Open Gideon menu'`, "overlay focus restoration");

  const deleted = routeHref(served.origin, "chat", { view: "detail", record: { kind: "chat_session", id: "removed-session" } });
  await browser.command("Page.navigate", { url: deleted });
  await browser.waitFor(`document.body.innerText.includes('The requested item may have been removed.')`, "deleted session recovery");
  await browser.evaluate(`Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Go to Chat')?.click()`);
  await browser.waitFor(`location.pathname==='/assistant/chat'&&document.getElementById('gideon-message-composer')`, "recovery to Chat");

  await browser.command("Network.setBlockedURLs", { urls: ["*gideon-console.css"] });
  const taskHref = routeHref(served.origin, "activity", {
    view: "workspace", record: { kind: "task", id: fixtureState.task_id }, placement: "tasks",
    returnTo: { destination: "chat", sessionId: session.key, selectionId, scrollY: 320 },
  });
  await browser.command("Page.navigate", { url: taskHref });
  await browser.waitFor(`document.body.innerText.includes('The trusted workspace could not be prepared.')`, "trusted module stylesheet failure");
  await browser.command("Network.setBlockedURLs", { urls: [] });
  await browser.evaluate(`Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Retry workspace')?.click()`);
  await browser.waitFor(`document.querySelector('.gideon-trusted-module[data-gideon-module="tasks"]')&&document.body.innerText.includes(${JSON.stringify(taskRecord.title)})`, "real task detail after module retry");
  assert.equal(await browser.evaluate(`document.querySelectorAll('#root').length`), 1);
  assert.equal(await browser.evaluate(`Boolean(document.querySelector('.gideon-trusted-module[data-gideon-module="tasks"]')?.closest('#root'))`), true);
  await browser.waitFor(`document.querySelector('.gideon-trusted-module[data-gideon-module="tasks"] main.gideon-workspace-frame[data-workspace-mode="full"] h1')?.textContent.trim()===${JSON.stringify(taskRecord.title)}&&[...document.querySelectorAll('button')].some(button=>button.textContent.trim()==='Edit')&&document.querySelector('textarea[aria-label="Comment"]')`, "native TaskDetail title, actions and full workspace frame");
  assert.equal(await browser.evaluate(`document.querySelectorAll('main').length`), 1);
  await browser.evaluate(`Array.from(document.querySelectorAll('button')).find(button=>button.textContent.trim()==='Edit')?.click()`);
  await browser.waitFor(`document.querySelector('input[aria-label="Title"]')?.value===${JSON.stringify(taskRecord.title)}`, "TaskDetail title edit control");
  await browser.evaluate(`Array.from(document.querySelectorAll('button')).find(button=>button.textContent.trim()==='Cancel')?.click()`);
  await browser.waitFor(`document.querySelector('.gideon-trusted-module[data-gideon-module="tasks"]')&&[...document.querySelectorAll('button')].some(button=>button.getAttribute('aria-label')==='Mark criterion complete')`, "native task exit criterion action");
  await browser.evaluate(`document.querySelector('[aria-label="Mark criterion complete"]')?.click()`);
  await browser.waitFor(`document.querySelector('[aria-label="Mark criterion incomplete"]')`, "native task criterion update");
  const taskFrame = await browser.evaluate<{ mode: string; viewport: number; width: number; right: number; scroll: number }>(`(()=>{const frame=document.querySelector('.gideon-trusted-module .gideon-workspace-frame');const r=frame.getBoundingClientRect();return {mode:frame.getAttribute('data-workspace-mode')||'',viewport:innerWidth,width:r.width,right:r.right,scroll:document.documentElement.scrollWidth}})()`);
  assert.equal(taskFrame.mode, "full");
  assert.ok(taskFrame.width > 0 && taskFrame.right <= taskFrame.viewport + 1 && taskFrame.scroll <= taskFrame.viewport + 1, JSON.stringify(taskFrame));
  const screenshots: string[] = [];
  for (const viewport of [
    { name: "desktop", width: 1280, height: 900, mobile: false },
    { name: "tablet", width: 768, height: 1024, mobile: true },
    { name: "phone", width: 360, height: 740, mobile: true },
  ]) {
    await browser.command("Emulation.setDeviceMetricsOverride", { width: viewport.width, height: viewport.height, deviceScaleFactor: 1, mobile: viewport.mobile });
    const frameGeometry: { rootCount: number; mainCount: number; width: number; right: number; scroll: number } = await browser.evaluate(`(()=>{const frame=document.querySelector('.gideon-trusted-module .gideon-workspace-frame');const r=frame.getBoundingClientRect();return {rootCount:document.querySelectorAll('#root').length,mainCount:document.querySelectorAll('main').length,width:r.width,right:r.right,scroll:document.documentElement.scrollWidth}})()`) as { rootCount: number; mainCount: number; width: number; right: number; scroll: number };
    assert.equal(frameGeometry.rootCount, 1);
    assert.equal(frameGeometry.mainCount, 1);
    assert.ok(frameGeometry.width > 0 && frameGeometry.right <= viewport.width + 1 && frameGeometry.scroll <= viewport.width + 1, `${viewport.name}: ${JSON.stringify(frameGeometry)}`);
    await new Promise(resolveFrame => setTimeout(resolveFrame, 150));
    const captured = await browser.command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    assert.equal(typeof captured.data, "string");
    const screenshotPath = join(visualEvidence, `task-detail-${viewport.name}.png`);
    await writeFile(screenshotPath, Buffer.from(String(captured.data), "base64"));
    screenshots.push(screenshotPath);
  }
  process.stdout.write(`Private responsive shell evidence: ${screenshots.join(", ")}\n`);
  await browser.command("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });

  const artifactHref = routeHref(served.origin, "apps", {
    view: "workspace", record: { kind: "artifact", id: fixtureState.artifact_slug }, placement: "artifacts/editor",
    returnTo: { destination: "chat", sessionId: session.key, selectionId, scrollY: 320 },
  });
  await browser.command("Page.addScriptToEvaluateOnNewDocument", { source: `window.__assistantE2eWorkerUrls=[];const NativeWorker=window.Worker;window.Worker=new Proxy(NativeWorker,{construct(target,args,newTarget){window.__assistantE2eWorkerUrls.push(new URL(String(args[0]),location.href).href);return Reflect.construct(target,args,newTarget)}});` });
  await browser.command("Page.navigate", { url: artifactHref });
  await browser.waitFor(`document.querySelector('.gideon-trusted-module[data-gideon-module="artifacts/editor"]')&&document.body.innerText.includes(${JSON.stringify(artifactRecord.name)})&&document.querySelector('[aria-label="Document view"] [aria-label="Edit"]')`, "native artifact edit view control");
  await browser.evaluate(`document.querySelector('[aria-label="Document view"] [aria-label="Edit"]')?.click()`);
  await browser.waitFor(`document.querySelector('.gideon-trusted-module[data-gideon-module="artifacts/editor"]')&&document.body.innerText.includes(${JSON.stringify(artifactRecord.name)})&&document.querySelector('.monaco-editor .native-edit-context[role="textbox"][aria-label="Trusted module editor — editor"]')`, "real artifact editor module and accessible Monaco textbox");
  assert.equal(await browser.evaluate(`Boolean(document.querySelector('.gideon-trusted-module[data-gideon-module="artifacts/editor"]')?.closest('#root'))`), true);
  const editorProof = await browser.evaluate<{ editor: boolean; accessibleName: string; modelText: string; moduleMode: string; workerUrls: string[]; mainBundleUrls: string[]; external: string[]; rootCount: number }>(`(()=>{
    const input=document.querySelector('.monaco-editor .native-edit-context[role="textbox"][aria-label]');
    const resources=performance.getEntriesByType('resource').map(entry=>entry.name);
    const workerUrls=[...new Set([...resources,...window.__assistantE2eWorkerUrls].filter(url=>/\/assistant\/assets\/workers\/gideon-monaco-[^/]+\.worker\.js(?:$|\?)/.test(url)))];
    return {editor:!!document.querySelector('.monaco-editor'),accessibleName:input?.getAttribute('aria-label')||'',modelText:document.querySelector('.monaco-editor .view-lines')?.textContent||'',moduleMode:document.querySelector('.gideon-trusted-module main.gideon-workspace-frame')?.getAttribute('data-workspace-mode')||'',workerUrls,mainBundleUrls:resources.filter(url=>new URL(url,location.href).origin===location.origin&&/_expo\/static\/js\/web\//.test(new URL(url,location.href).pathname)&&/\.(?:js|bundle)$/.test(new URL(url,location.href).pathname)),external:resources.filter(url=>/https?:\/\/(?:[^/]+\.)?(?:jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com)\//i.test(url)),rootCount:document.querySelectorAll('#root').length};
  })()`);
  assert.equal(editorProof.editor, true);
  assert.match(editorProof.accessibleName, /Trusted module editor.*editor/i);
  assert.ok(editorProof.modelText.includes("A real native artifact"), "Monaco rendered the fixture's native markdown model");
  assert.match(editorProof.moduleMode, /full/);
  const expectedWorkerNames = ["editor", "json", "css", "html", "typescript"];
  for (const family of expectedWorkerNames) assert.ok(editorProof.workerUrls.some(url => url.includes(`/gideon-monaco-${family}.worker.js`)), `native Monaco ${family} worker URL was requested`);
  assert.ok(editorProof.mainBundleUrls.length > 0, "the local Expo main JavaScript bundle initialized the real Monaco editor");
  assert.deepEqual(editorProof.external, []);
  assert.equal(editorProof.rootCount, 1);
  await browser.evaluate(`(()=>{window.__assistantE2eEditor=document.querySelector('.monaco-editor');document.querySelector('.monaco-editor .native-edit-context[role="textbox"][aria-label="Trusted module editor — editor"]')?.focus();return true})()`);
  await browser.waitFor(`document.activeElement?.matches('.monaco-editor .native-edit-context[role="textbox"][aria-label="Trusted module editor — editor"]')`, "keyboard focus in accessible Monaco editor");
  await browser.command("Input.insertText", { text: "Keyboard operation marker" });
  await browser.waitFor(`document.querySelector('.monaco-editor .view-lines')?.textContent.includes('Keyboard operation marker')`, "keyboard edit in the native artifact model");

  const shellBeforeTheme = await browser.evaluate<{ htmlClass: string; htmlMode: string | null; storedTheme: string | null; editor: boolean }>(`({htmlClass:document.documentElement.className,htmlMode:document.documentElement.getAttribute('data-mode'),storedTheme:localStorage.getItem('gideon-assistant-theme'),editor:!!window.__assistantE2eEditor})`);
  await browser.evaluate(`document.querySelector('[aria-label="Open Gideon menu"]')?.click()`);
  await browser.waitFor(`document.querySelector('[data-gideon-assistant-overlay]')`, "theme menu while editor is active");
  await browser.evaluate(`document.querySelector('[aria-label="Dark theme"]')?.click()`);
  await browser.waitFor(`document.querySelector('[data-gideon-assistant][data-theme="dark"]')&&document.querySelector('[data-gideon-assistant-overlay][data-theme="dark"]')`, "shell and overlay theme update");
  const shellAfterTheme = await browser.evaluate<{ htmlClass: string; htmlMode: string | null; storedTheme: string | null; sameEditor: boolean; rootCount: number }>(`({htmlClass:document.documentElement.className,htmlMode:document.documentElement.getAttribute('data-mode'),storedTheme:localStorage.getItem('gideon-assistant-theme'),sameEditor:window.__assistantE2eEditor===document.querySelector('.monaco-editor'),rootCount:document.querySelectorAll('#root').length})`);
  assert.equal(shellAfterTheme.htmlClass, shellBeforeTheme.htmlClass);
  assert.equal(shellAfterTheme.htmlMode, shellBeforeTheme.htmlMode);
  assert.equal(shellAfterTheme.storedTheme, "dark");
  assert.equal(shellAfterTheme.sameEditor, true, "theme change preserves the initialized Monaco editor");
  assert.equal(shellAfterTheme.rootCount, 1);
  await browser.command("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await browser.command("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await browser.waitFor(`!document.querySelector('[data-gideon-assistant-overlay]')&&document.activeElement?.getAttribute('aria-label')==='Open Gideon menu'`, "overlay focus restoration in module route");
  await browser.evaluate(`document.querySelector('[aria-label="Return to previous workspace"]')?.click()`);
  await browser.waitFor(`location.pathname==='/assistant/chat'&&new URLSearchParams(location.search).get('session')===${JSON.stringify(session.key)}&&new URLSearchParams(location.search).get('fromSelection')===${JSON.stringify(selectionId)}&&new URLSearchParams(location.search).get('fromScroll')==='320'`, "return to the same conversation, selected message and scroll context");
  await browser.waitFor(`(()=>{const message=document.getElementById(${JSON.stringify(`gideon-message-${selectionId}`)});const list=document.getElementById('gideon-chat-scroll');return !!message&&getComputedStyle(message).borderTopWidth==='2px'&&!!list&&list.scrollTop>=300})()`, "selected message highlight and conversation scroll restoration");
  assert.equal(await browser.evaluate(`Boolean(document.getElementById('gideon-message-composer'))`), true);
  assert.equal(await browser.evaluate(`Boolean(document.querySelector('[aria-label="Return to previous workspace"]'))`), false, "returning to the source conversation does not create a self-return action");
  assert.equal(await browser.evaluate(`document.querySelectorAll('#root').length`), 1);
});
