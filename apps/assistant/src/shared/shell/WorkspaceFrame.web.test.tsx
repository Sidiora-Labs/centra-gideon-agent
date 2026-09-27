import * as React from "react";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { renderToStaticMarkup } from "react-dom/server";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer as createNetServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildSync } from "esbuild";
import { describe, expect, it } from "vitest";
import { WorkspaceFrame, workspaceFrameStyle } from "./WorkspaceFrame.web";
import { createShellRoute } from "./shellRoutes";

describe("assistant workspace frame", () => {
  it("defines centered reading and full-width workspace geometry", () => {
    expect(workspaceFrameStyle("compact")).toMatchObject({ width: "100%", maxWidth: 760, marginInline: "auto", minHeight: 0 });
    expect(workspaceFrameStyle("full")).toMatchObject({ width: "100%", height: "100%", maxWidth: "none", minHeight: 0 });
  });

  it("renders distinct empty, permission and failure recovery controls", () => {
    const route = createShellRoute("apps");
    const empty = renderToStaticMarkup(<WorkspaceFrame route={route} mode="full" title="Apps"
      state={{ kind: "empty", message: "No apps are connected." }} onGoToChat={() => {}}>Workspace content</WorkspaceFrame>);
    const denied = renderToStaticMarkup(<WorkspaceFrame route={route} mode="full" title="Apps"
      state={{ kind: "denied", message: "You do not have access." }} onGoToChat={() => {}}>Restricted content</WorkspaceFrame>);
    const error = renderToStaticMarkup(<WorkspaceFrame route={route} mode="full" title="Apps"
      state={{ kind: "error", message: "The workspace could not be loaded.", onRetry: () => {} }}>Unavailable content</WorkspaceFrame>);
    expect(empty).toContain('data-workspace-state="empty"');
    expect(empty).toContain("No apps are connected.");
    expect(empty).toContain("Workspace content");
    expect(denied).toContain('role="alert"');
    expect(denied).toContain("You do not have access.");
    expect(denied).toContain("Go to Chat");
    expect(denied).not.toContain("Restricted content");
    expect(error).toContain("The workspace could not be loaded.");
    expect(error).toContain(">Retry</button>");
    expect(error).not.toContain("Unavailable content");
  });

  it("keeps route focus and scroll, uses available workspace width, and exposes mobile state actions in Chromium", async () => {
    const sourceDirectory = process.cwd();
    const browserSource = `
      import * as React from 'react';
      import { createRoot } from 'react-dom/client';
      import { WorkspaceFrame } from './src/shared/shell/WorkspaceFrame.web';
      import type { WorkspaceFrameState } from './src/shared/shell/WorkspaceFrame.web';
      import { createShellRoute } from './src/shared/shell/shellRoutes';
      function Harness() {
        const chat = createShellRoute('chat', { view: 'workspace' });
        const app = createShellRoute('apps', { view: 'workspace', placement: { id: 'app-workspace' } });
        const [route, setRoute] = React.useState(chat);
        const [state, setState] = React.useState<WorkspaceFrameState>({ kind: 'ready' });
        const [retries, setRetries] = React.useState(0);
        return <><nav>
          <button id="open-full" onClick={() => setRoute(app)}>Open full workspace</button>
          <button id="return-chat" onClick={() => setRoute(chat)}>Return to chat</button>
          <button id="show-loading" onClick={() => setState({ kind: 'loading', message: 'Checking workspace…' })}>Loading</button>
          <button id="show-empty" onClick={() => setState({ kind: 'empty', message: 'Nothing here yet.' })}>Empty</button>
          <button id="show-denied" onClick={() => setState({ kind: 'denied', message: 'Access is required.' })}>Permission</button>
          <button id="show-error" onClick={() => setState({ kind: 'error', message: 'Workspace unavailable.', onRetry: () => setRetries(value => value + 1) })}>Failure</button>
        </nav><output id="retry-count">{retries}</output>
          <div style={{ height: 'calc(100vh - 40px)', width: '100%' }}>
            <WorkspaceFrame route={route} mode={route.destination === 'chat' ? 'compact' : 'full'} title={route.destination === 'chat' ? 'Chat' : 'Apps'}
              state={state} onGoToChat={() => setRoute(chat)}>
              <section style={{ minHeight: route.destination === 'chat' ? 1400 : 180 }}><p>Workspace content</p><button id="workspace-action">Workspace action</button></section>
            </WorkspaceFrame>
          </div></>;
      }
      createRoot(document.getElementById('root')).render(<Harness />);
    `;
    const outputs = buildSync({ stdin: { contents: browserSource, resolveDir: sourceDirectory, loader: "tsx" },
      bundle: true, platform: "browser", format: "iife", write: false, outfile: join(tmpdir(), "gideon-workspace-frame.js"),
      resolveExtensions: [".web.tsx", ".web.ts", ".tsx", ".ts", ".jsx", ".js", ".json"],
      alias: { "react-native": "react-native-web" }, define: { "process.env.NODE_ENV": '"production"', __DEV__: "false" }, logLevel: "silent" }).outputFiles;
    const script = outputs.find(file => file.path.endsWith(".js"))?.text;
    const style = outputs.find(file => file.path.endsWith(".css"))?.text ?? "";
    if (!script) throw new Error("Chromium frame bundle was not created");
    const directory = await mkdtemp(join(tmpdir(), "gideon-workspace-frame-"));
    const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>${style}</style></head><body style="margin:0"><div id="root"></div><script>${script}</script></body></html>`;
    const server = createServer((_request, response) => {
      response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      response.end(html);
    });
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    const debugServer = createNetServer();
    await new Promise<void>(resolve => debugServer.listen(0, "127.0.0.1", resolve));
    const debugAddress = debugServer.address();
    if (!debugAddress || typeof debugAddress === "string") throw new Error("Chromium debug port unavailable");
    await new Promise<void>(resolve => debugServer.close(() => resolve()));
    const profile = join(directory, "chromium");
    const browser = spawn(process.env.CHROMIUM_BIN || "chromium", ["--headless=new", "--no-sandbox", "--disable-gpu",
      "--disable-background-networking", `--remote-debugging-port=${debugAddress.port}`, `--user-data-dir=${profile}`, "about:blank"],
    { stdio: ["ignore", "ignore", "pipe"] });
    let errors = "";
    browser.stderr.on("data", chunk => { errors = (errors + String(chunk)).slice(-3000); });
    let socket: WebSocket | undefined;
    try {
      let target: { webSocketDebuggerUrl: string } | undefined;
      for (let attempt = 0; attempt < 100 && !target; attempt += 1) {
        try { target = ((await (await fetch(`http://127.0.0.1:${debugAddress.port}/json/list`)).json()) as Array<{ type: string; webSocketDebuggerUrl: string }>).find(item => item.type === "page"); }
        catch { await new Promise(resolve => setTimeout(resolve, 100)); }
      }
      if (!target) throw new Error(`Chromium debugger did not start: ${errors}`);
      socket = new WebSocket(target.webSocketDebuggerUrl);
      await new Promise<void>((resolve, reject) => {
        socket!.addEventListener("open", () => resolve(), { once: true });
        socket!.addEventListener("error", () => reject(new Error("Chromium debugger connection failed")), { once: true });
      });
      let id = 0;
      const pending = new Map<number, { resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void }>();
      socket.addEventListener("message", event => {
        const message = JSON.parse(String(event.data)) as { id?: number; result?: Record<string, unknown>; error?: { message: string } };
        if (message.id === undefined) return;
        const waiter = pending.get(message.id);
        if (!waiter) return;
        pending.delete(message.id);
        if (message.error) waiter.reject(new Error(message.error.message)); else waiter.resolve(message.result ?? {});
      });
      const send = (method: string, params: Record<string, unknown> = {}) => new Promise<Record<string, unknown>>((resolve, reject) => {
        const requestId = ++id;
        pending.set(requestId, { resolve, reject });
        socket!.send(JSON.stringify({ id: requestId, method, params }));
      });
      const evaluate = async <T,>(expression: string): Promise<T> => {
        const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }) as {
          result?: { value?: T }; exceptionDetails?: { text?: string; exception?: { description?: string } };
        };
        if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text || "Browser expression failed");
        return result.result?.value as T;
      };
      const waitForHeading = (title: string) => evaluate<boolean>(`(() => new Promise(resolve => {
        const frame = document.querySelector('[data-workspace-mode]');
        const heading = frame?.querySelector('h1');
        if (heading?.textContent.trim() === ${JSON.stringify(title)}) { resolve(true); return; }
        const observer = new MutationObserver(() => {
          if (frame?.querySelector('h1')?.textContent.trim() === ${JSON.stringify(title)}) {
            observer.disconnect();
            resolve(true);
          }
        });
        observer.observe(frame, { childList: true, characterData: true, subtree: true });
      }))()`);
      await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("Frame test server unavailable");
      await send("Page.navigate", { url: `http://127.0.0.1:${address.port}/` });
      for (let attempt = 0; attempt < 100 && !(await evaluate<boolean>("Boolean(document.querySelector('[data-workspace-mode]'))")); attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      const initial = await evaluate<{ width: number; left: number; focus: string }>(`(() => {
        const frame = document.querySelector('[data-workspace-mode]'); const rect = frame.getBoundingClientRect();
        return { width: rect.width, left: rect.left, focus: document.activeElement.textContent.trim() };
      })()`);
      const scrolledTop = await evaluate<number>(`(() => new Promise(resolve => {
        const body = document.querySelector('[data-workspace-scroll]');
        body.addEventListener('scroll', () => resolve(body.scrollTop), { once: true });
        body.scrollTop = 420;
      }))()`);
      await evaluate("document.getElementById('open-full').click()");
      await waitForHeading("Apps");
      const full = await evaluate<{ width: number; height: number; heading: string; focused: boolean; scrollTop: number; scrollHeight: number; clientHeight: number }>(`(() => {
        const frame = document.querySelector('[data-workspace-mode]'); const rect = frame.getBoundingClientRect(); const heading = frame.querySelector('h1');
        const body = document.querySelector('[data-workspace-scroll]');
        return { width: rect.width, height: rect.height, heading: heading.textContent.trim(), focused: document.activeElement === heading,
          scrollTop: body.scrollTop, scrollHeight: body.scrollHeight, clientHeight: body.clientHeight };
      })()`);
      await evaluate("document.getElementById('return-chat').click()");
      await waitForHeading("Chat");
      const restored = await evaluate<{ top: number; focused: boolean }>(`(() => ({
        top: document.querySelector('[data-workspace-scroll]').scrollTop,
        focused: document.activeElement === document.querySelector('h1')
      }))()`);
      await send("Emulation.setDeviceMetricsOverride", { width: 360, height: 640, deviceScaleFactor: 1, mobile: true });
      await evaluate("document.getElementById('show-loading').click()");
      const loading = await evaluate<boolean>(`document.querySelector('[data-workspace-mode]').getAttribute('aria-busy') === 'true' && Boolean(document.querySelector('[role="status"]'))`);
      await evaluate("document.getElementById('show-empty').click()");
      const empty = await evaluate<boolean>(`document.querySelector('[data-workspace-mode]').dataset.workspaceState === 'empty' && document.body.innerText.includes('Nothing here yet.')`);
      await evaluate("document.getElementById('show-denied').click()");
      const permission = await evaluate<boolean>(`document.querySelector('[data-workspace-mode]').dataset.workspaceState === 'denied' && Boolean(document.querySelector('[role="alert"]')) && [...document.querySelectorAll('button')].some(button => button.textContent === 'Go to Chat')`);
      const mobile = await evaluate<{ width: number; right: number; viewport: number; actionHeight: number }>(`(() => {
        const frame = document.querySelector('[data-workspace-mode]'); const action = [...document.querySelectorAll('.gideon-workspace-recovery')].find(button => button.textContent === 'Go to Chat').getBoundingClientRect();
        return { width: frame.getBoundingClientRect().width, right: action.right, viewport: innerWidth, actionHeight: action.height };
      })()`);
      await evaluate("document.getElementById('show-error').click()");
      const retry = await evaluate<{ visible: boolean; count: string }>(`(() => new Promise(resolve => {
        const button = [...document.querySelectorAll('button')].find(item => item.textContent === 'Retry');
        const output = document.getElementById('retry-count');
        if (!button || !output) { resolve({ visible: false, count: '' }); return; }
        const rect = button.getBoundingClientRect();
        const visible = rect.width > 0 && rect.right <= innerWidth;
        const observer = new MutationObserver(() => {
          if (output.textContent === '1') {
            observer.disconnect();
            resolve({ visible, count: output.textContent });
          }
        });
        observer.observe(output, { childList: true, characterData: true, subtree: true });
        button.click();
        if (output.textContent === '1') {
          observer.disconnect();
          resolve({ visible, count: output.textContent });
        }
      }))()`);
      expect(initial.width).toBe(760);
      expect(Math.abs(initial.left - 260)).toBeLessThanOrEqual(1);
      expect(initial.focus).toBe("Chat");
      expect(full.width).toBe(1280);
      expect(full.height).toBeGreaterThan(500);
      expect(full.heading).toBe("Apps");
      expect(full.focused).toBe(true);
      expect(scrolledTop).toBe(420);
      expect(full.scrollHeight).toBeLessThanOrEqual(full.clientHeight);
      expect(full.scrollTop).toBe(0);
      expect(restored).toEqual({ top: 420, focused: true });
      expect(mobile.width).toBe(360);
      expect(mobile.right).toBeLessThanOrEqual(mobile.viewport);
      expect(mobile.actionHeight).toBeGreaterThanOrEqual(44);
      expect({ loading, empty, permission, retry }).toEqual({ loading: true, empty: true, permission: true, retry: { visible: true, count: "1" } });
    } finally {
      socket?.close();
      browser.kill("SIGTERM");
      server.close();
      await rm(directory, { recursive: true, force: true });
    }
  }, 30000);
});
