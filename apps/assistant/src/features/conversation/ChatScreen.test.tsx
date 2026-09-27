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
let debuggerSocket: WebSocket | undefined;

afterAll(async () => {
  debuggerSocket?.close();
  for (const child of children) child.kill("SIGTERM");
  if (vite) await vite.close();
  for (const directory of directories) await rm(directory, { recursive: true, force: true });
});

async function port(): Promise<number> {
  const server = createNetServer();
  await new Promise<void>(done => server.listen(0, "127.0.0.1", done));
  const address = server.address();
  const value = typeof address === "object" && address ? address.port : 0;
  await new Promise<void>(done => server.close(() => done()));
  return value;
}

async function startConversationServer(origin: string): Promise<string> {
  const child = spawn(process.env.GIDEON_TEST_PYTHON || "python3", [
    join(root, "apps/assistant/test-support/conversation_server.py"), origin,
  ], { env: { ...process.env, PYTHONPATH: join(root, "runtime") } });
  children.push(child);
  let output = "";
  let errors = "";
  return new Promise<string>((resolveAddress, reject) => {
    const timeout = setTimeout(() => reject(new Error(`Conversation server timed out: ${errors}`)), 15000);
    child.stdout.on("data", chunk => {
      output += String(chunk);
      if (!output.includes("\n")) return;
      clearTimeout(timeout);
      const line = output.split("\n")[0];
      try {
        const address = JSON.parse(line) as { api_port: number };
        resolveAddress(`http://127.0.0.1:${address.api_port}`);
      } catch (error) { reject(error); }
    });
    child.stderr.on("data", chunk => { errors += String(chunk); });
    child.once("exit", code => {
      clearTimeout(timeout);
      reject(new Error(`Conversation server exited ${code}: ${errors}`));
    });
  });
}

async function launchBrowser(address: string) {
  const directory = await mkdtemp(join(tmpdir(), "gideon-chat-screen-"));
  directories.push(directory);
  const debuggingPort = await port();
  const child = spawn(process.env.CHROMIUM_BIN || "chromium", [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking",
    "--no-first-run", `--remote-debugging-port=${debuggingPort}`,
    `--user-data-dir=${directory}`, "about:blank",
  ], { env: Object.fromEntries(Object.entries(process.env).filter(([key]) => key !== "OPENAI_API_KEY")) });
  children.push(child);
  let target: { webSocketDebuggerUrl: string } | undefined;
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${debuggingPort}/json`)).json() as
        Array<{ type: string; webSocketDebuggerUrl: string }>;
      target = targets.find(item => item.type === "page");
      if (target) break;
    } catch { await new Promise(done => setTimeout(done, 100)); }
    if (!target) await new Promise(done => setTimeout(done, 100));
  }
  if (!target) throw new Error("Chromium did not start");
  debuggerSocket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise<void>((done, reject) => {
    debuggerSocket!.addEventListener("open", () => done(), { once: true });
    debuggerSocket!.addEventListener("error", () => reject(new Error("Chromium debugger failed")), { once: true });
  });
  let nextId = 0;
  const pending = new Map<number, { done: (value: any) => void; reject: (error: Error) => void }>();
  debuggerSocket.addEventListener("message", event => {
    const response = JSON.parse(String(event.data)) as { id?: number; result?: any; error?: { message: string } };
    if (!response.id) return;
    const request = pending.get(response.id);
    if (!request) return;
    pending.delete(response.id);
    if (response.error) request.reject(new Error(response.error.message));
    else request.done(response.result);
  });
  const command = (method: string, params: Record<string, unknown> = {}) => new Promise<any>((done, reject) => {
    const id = ++nextId;
    pending.set(id, { done, reject });
    debuggerSocket!.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression: string) => {
    const response = await command("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
    return response.result?.value;
  };
  await command("Page.navigate", { url: address });
  return { evaluate, command };
}

describe("Gideon conversation presentation", () => {
  it("sends through the canonical controller and retains draft, session and return context across remount", async () => {
    const webPort = await port();
    const origin = `http://127.0.0.1:${webPort}`;
    const api = await startConversationServer(origin);
    const entry = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { ChatScreen } from '/src/features/conversation/ChatScreen.tsx';
import { ConversationController } from '/src/shared/conversation/controller.ts';
import { ownerScope, signInOwner } from '/src/shared/auth.web.tsx';
import { ShellThemeProvider } from '/src/shared/shell/shellTheme.web.ts';
const controller = new ConversationController();
const root = createRoot(document.getElementById('root'));
let scope;
window.begin = async () => {
  const owner = await signInOwner('conversation-owner', 'correct-horse-battery-staple');
  scope = ownerScope(location.origin, owner);
  window.mountChat();
  return true;
};
window.mountChat = (sessionId, scrollY) => root.render(React.createElement(
  ShellThemeProvider, { initialPreference: 'light' },
  React.createElement(ChatScreen, { controller, scope, sessionId, scrollY,
    returnTo: { destination: 'apps', selectionId: 'application-1' },
    onScrollChange: value => { window.savedScroll = value; },
    onReturn: () => { window.returned = (window.returned || 0) + 1; } })
));
window.unmountChat = () => root.render(null);
window.snapshot = () => controller.snapshot();
window.disposeController = () => controller.dispose();
window.ready = true;
`;
    vite = await createServer({
      configFile: false,
      root: join(root, "apps/assistant"),
      resolve: {
        alias: [{ find: /^react-native$/, replacement: "react-native-web" }],
        extensions: [".web.tsx", ".web.ts", ".tsx", ".ts", ".jsx", ".js", ".json"],
      },
      plugins: [{
        name: "gideon-chat-screen-entry",
        resolveId(id) { if (id === "/chat-screen-entry.ts") return "\0chat-screen-entry"; },
        load(id) { if (id === "\0chat-screen-entry") return entry; },
        configureServer(server) {
          server.middlewares.use("/chat-screen", (_request, response) => {
            response.setHeader("Content-Type", "text/html; charset=utf-8");
            response.end('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div><script type="module" src="/chat-screen-entry.ts"></script>');
          });
        },
      }],
      server: { host: "127.0.0.1", port: webPort, strictPort: true, proxy: { "/api": { target: api, ws: true } } },
    });
    await vite.listen();
    const { evaluate, command } = await launchBrowser(`${origin}/chat-screen`);
    await evaluate('new Promise((done, reject) => { let n=0; const timer=setInterval(() => { if (window.ready) { clearInterval(timer); done(true) } else if (++n > 100) { clearInterval(timer); reject(Error("screen entry unavailable")) } }, 50) })');
    await evaluate("window.begin()");
    const mounted = await evaluate(`new Promise((done, reject) => { let n=0; const timer=setInterval(() => {
      const input = document.getElementById('gideon-message-composer');
      if (input && input.getAttribute('aria-label') === 'Message Gideon') { clearInterval(timer); done(true); }
      else if (++n > 150) { clearInterval(timer); reject(Error('Gideon composer did not mount')); }
    }, 50) })`);
    expect(mounted).toBe(true);
    expect(await evaluate("document.body.innerText.includes('A little help. A lot more room for life.')")).toBe(true);
    expect(await evaluate("document.querySelector('[aria-label=\\\"Send message\\\"]')?.getAttribute('aria-disabled')")).toBe("true");

    const prompt = "Reply with one word: ready.";
    await evaluate(`(() => {
      const input = document.getElementById('gideon-message-composer');
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value').set;
      setter.call(input, ${JSON.stringify(prompt)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    await evaluate("document.querySelector('[aria-label=\\\"Send message\\\"]')?.click()");
    const submitted = await evaluate(`new Promise((done, reject) => { let n=0; const timer=setInterval(() => {
      const state = window.snapshot();
      if (state.sessionId && state.messages.some(message => message.role === 'user' && message.content === ${JSON.stringify(prompt)})) {
        clearInterval(timer); done({ sessionId: state.sessionId, draft: state.draft, messageCount: state.messages.length });
      } else if (++n > 200) { clearInterval(timer); reject(Error('The canonical conversation did not receive the UI submission: ' + JSON.stringify(state))); }
    }, 50) })`);
    expect(submitted.sessionId).toMatch(/^chat-/);
    expect(submitted.draft).toBe("");

    const retainedDraft = "Keep this draft when I return.";
    await evaluate(`(() => {
      const input = document.getElementById('gideon-message-composer');
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value').set;
      setter.call(input, ${JSON.stringify(retainedDraft)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    await evaluate("window.unmountChat()");
    await evaluate("new Promise(done => setTimeout(done, 50))");
    await evaluate(`window.mountChat(${JSON.stringify(submitted.sessionId)}, 0)`);
    const returned = await evaluate(`new Promise((done, reject) => { let n=0; const timer=setInterval(() => {
      const state = window.snapshot();
      const input = document.getElementById('gideon-message-composer');
      if (input && state.sessionId === ${JSON.stringify(submitted.sessionId)} && state.draft === ${JSON.stringify(retainedDraft)}
        && state.messages.some(message => message.role === 'user' && message.content === ${JSON.stringify(prompt)})) {
        clearInterval(timer); done({ draft: input.value, scroll: document.getElementById('gideon-chat-scroll')?.scrollTop });
      } else if (++n > 100) { clearInterval(timer); reject(Error('Controller state did not survive route remount')); }
    }, 50) })`);
    expect(returned.draft).toBe(retainedDraft);
    expect(returned.scroll).toBe(0);
    await evaluate("document.querySelector('[aria-label=\\\"Return to previous workspace\\\"]')?.click()");
    expect(await evaluate("window.returned")).toBe(1);
    await evaluate("window.disposeController()");
  }, 30000);
});
