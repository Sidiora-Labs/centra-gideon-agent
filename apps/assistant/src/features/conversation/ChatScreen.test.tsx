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
const browserDiagnostics: string[] = [];

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

async function startConversationServer(origin: string): Promise<{ api: string; rotateOwner(username: string): Promise<void> }> {
  const child = spawn(process.env.GIDEON_TEST_PYTHON || "python3", [
    join(root, "apps/assistant/test-support/conversation_server.py"), origin,
  ], { env: { ...process.env, PYTHONPATH: join(root, "runtime") } });
  children.push(child);
  let output = "";
  let errors = "";
  let pendingRotation: { username: string; resolve: () => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> } | undefined;
  let resolveAddress!: (address: string) => void;
  let rejectAddress!: (error: Error) => void;
  const addressReady = new Promise<string>((resolve, reject) => { resolveAddress = resolve; rejectAddress = reject; });
  child.stdout.on("data", chunk => {
    output += String(chunk);
    const lines = output.split("\n");
    output = lines.pop() || "";
    for (const line of lines) {
      if (!line) continue;
      try {
        const message = JSON.parse(line) as { api_port?: number; owner_rotated?: string; control_error?: string };
        if (message.api_port) resolveAddress(`http://127.0.0.1:${message.api_port}`);
        else if (pendingRotation && message.owner_rotated === pendingRotation.username) {
          clearTimeout(pendingRotation.timer);
          pendingRotation.resolve();
          pendingRotation = undefined;
        } else if (pendingRotation && message.control_error) {
          clearTimeout(pendingRotation.timer);
          pendingRotation.reject(new Error(message.control_error));
          pendingRotation = undefined;
        }
      } catch { /* Ignore non-JSON server diagnostics. */ }
    }
  });
  child.stderr.on("data", chunk => { errors += String(chunk); });
  child.once("exit", code => {
    rejectAddress(new Error(`Conversation server exited ${code}: ${errors}`));
    if (pendingRotation) {
      clearTimeout(pendingRotation.timer);
      pendingRotation.reject(new Error(`Conversation server exited ${code}: ${errors}`));
      pendingRotation = undefined;
    }
  });
  const api = await new Promise<string>((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`Conversation server timed out: ${errors}`)), 15000);
    void addressReady.then(address => { clearTimeout(timeout); resolve(address); }, error => { clearTimeout(timeout); reject(error); });
  });
  return {
    api,
    rotateOwner(username) {
      if (pendingRotation) return Promise.reject(new Error("An owner rotation is already pending"));
      return new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => {
          pendingRotation = undefined;
          reject(new Error(`Credential rotation timed out for ${username}`));
        }, 15000);
        pendingRotation = { username, resolve, reject, timer };
        child.stdin.write(`${JSON.stringify({ rotate_owner: username })}\n`);
      });
    },
  };
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
    const notification = response as typeof response & { method?: string; params?: any };
    if (notification.method === "Runtime.exceptionThrown") {
      browserDiagnostics.push(`exception ${JSON.stringify(notification.params?.exceptionDetails)}`);
    } else if (notification.method === "Runtime.consoleAPICalled") {
      browserDiagnostics.push(`console ${JSON.stringify(notification.params)}`);
    } else if (notification.method === "Log.entryAdded") {
      browserDiagnostics.push(`log ${JSON.stringify(notification.params?.entry)}`);
    } else if (notification.method === "Network.loadingFailed") {
      browserDiagnostics.push(`network-failed ${JSON.stringify(notification.params)}`);
    } else if (notification.method === "Network.responseReceived") {
      const response = notification.params?.response;
      if (response?.url?.startsWith(address)) browserDiagnostics.push(`response ${response.status} ${response.url}`);
    }
    if (!response.id) return;
    const request = pending.get(response.id);
    if (!request) return;
    pending.delete(response.id);
    if (response.error) request.reject(new Error(response.error.message));
    else request.done(response.result);
  });
  const command = (method: string, params: Record<string, unknown> = {}) => new Promise<any>((done, reject) => {
    const id = ++nextId;
    const timeout = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`Chromium CDP command timed out: ${method}; browser=${JSON.stringify(browserDiagnostics)}`));
    }, 10000);
    pending.set(id, {
      done: value => { clearTimeout(timeout); done(value); },
      reject: error => { clearTimeout(timeout); reject(error); },
    });
    debuggerSocket!.send(JSON.stringify({ id, method, params }));
  });
  await command("Runtime.enable");
  await command("Log.enable");
  await command("Network.enable");
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
    const conversationServer = await startConversationServer(origin);
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
let commitSequence = 0;
function CommitProbe({ sequence }) {
  React.useLayoutEffect(() => {
    if (!window.captureFirstCommit) return;
    window.captureFirstCommit = false;
    const input = document.getElementById('gideon-message-composer');
    const send = document.querySelector('[aria-label="Send message"]');
    const returned = document.querySelector('[aria-label="Return to previous workspace"]');
    const firstCommit = {
      targetOwner: scope.ownerId,
      controllerOwner: controller.snapshot().scope?.ownerId || null,
      targetSession: window.targetSession || null,
      controllerSession: controller.snapshot().sessionId,
      inputValue: input?.value ?? null,
      inputDisabled: input?.disabled ?? null,
      inputReadOnly: input?.readOnly ?? null,
      sendDisabled: send?.getAttribute('aria-disabled') === 'true' || send?.disabled === true,
      returnDisabled: returned?.getAttribute('aria-disabled') === 'true' || returned?.disabled === true,
      body: document.body.innerText,
    };
    if (window.exerciseMismatchEvents && input) {
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value').set;
      setter.call(input, 'This event must not replace the prior owner draft.');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      send?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    }
    firstCommit.afterEvents = {
      draft: controller.snapshot().draft,
      phase: controller.snapshot().phase,
      messageCount: controller.snapshot().messages.length,
    };
    window.firstCommit = firstCommit;
  }, [sequence]);
  return null;
}
window.captureNextCommit = (exerciseMismatchEvents = false) => {
  window.firstCommit = undefined;
  window.exerciseMismatchEvents = exerciseMismatchEvents;
  window.captureFirstCommit = true;
};
window.targetSession = null;
window.renderChat = (nextSessionId, scrollY, selectedMessageId) => {
  window.targetSession = nextSessionId || null;
  return root.render(React.createElement(React.Fragment, null,
    React.createElement(CommitProbe, { sequence: ++commitSequence }),
    React.createElement(ShellThemeProvider, { initialPreference: 'light' },
      React.createElement(ChatScreen, { controller, scope, sessionId: nextSessionId, scrollY, selectedMessageId,
        returnTo: { destination: 'apps', sessionId: nextSessionId, selectionId: selectedMessageId, scrollY },
        onScrollChange: value => { window.savedScroll = value; },
        onReturn: () => { window.returned = (window.returned || 0) + 1; } }))));
};
window.begin = async () => {
  const owner = await signInOwner('conversation-owner', 'correct-horse-battery-staple');
  scope = ownerScope(location.origin, owner);
  window.renderChat(undefined);
  return true;
};
window.switchToSecondOwner = async () => {
  const owner = await signInOwner('conversation-owner-two', 'correct-horse-battery-staple');
  scope = ownerScope(location.origin, owner);
  window.expectedOwnerCacheKey = scope.cacheKey;
  window.renderChat(undefined);
  return owner.user;
};
window.mountChat = window.renderChat;
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
      esbuild: { jsx: "automatic" },
      optimizeDeps: {
        include: ["react", "react-dom/client", "react/jsx-runtime", "react/jsx-dev-runtime", "react-native-web"],
        esbuildOptions: { resolveExtensions: [".web.tsx", ".web.ts", ".tsx", ".ts", ".jsx", ".js", ".json"] },
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
      server: { host: "127.0.0.1", port: webPort, strictPort: true, proxy: { "/api": { target: conversationServer.api, ws: true } } },
    });
    await vite.listen();
    const { evaluate, command } = await launchBrowser(`${origin}/chat-screen`);
    await evaluate('new Promise((done, reject) => { let n=0; const timer=setInterval(() => { if (window.ready) { clearInterval(timer); done(true) } else if (++n > 100) { clearInterval(timer); reject(Error("screen entry unavailable")) } }, 50) })');
    await evaluate("window.begin()");
    const mounted = await evaluate(`new Promise((done, reject) => { let n=0; const timer=setInterval(() => {
      const input = document.getElementById('gideon-message-composer');
      if (input && input.getAttribute('aria-label') === 'Message Gideon') { clearInterval(timer); done(true); }
      else if (++n > 150) {
        clearInterval(timer);
        fetch(location.href).then(response => response.status).catch(error => String(error)).then(entryStatus => {
          reject(Error('Gideon composer did not mount; location=' + location.href + '; entryStatus=' + entryStatus + '; body=' + document.body.innerText));
        });
      }
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
    const selectedMessageId = await evaluate(`window.snapshot().messages.find(message => message.role === 'user' && message.content === ${JSON.stringify(prompt)}).id`);
    await evaluate(`window.mountChat(${JSON.stringify(submitted.sessionId)}, 0, ${JSON.stringify(selectedMessageId)})`);
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
    expect(await evaluate(`document.getElementById(${JSON.stringify(`gideon-message-${selectedMessageId}`)})?.style.borderWidth`)).toBe("2px");
    await evaluate("document.querySelector('[aria-label=\\\"Return to previous workspace\\\"]')?.click()");
    expect(await evaluate("window.returned")).toBe(1);

    const nextSession = await evaluate(`fetch('/api/chat/sessions',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'},body:'{}'}).then(response=>response.json())`);
    expect(typeof nextSession.key).toBe("string");
    await evaluate("window.captureNextCommit(true)");
    await evaluate(`window.renderChat(${JSON.stringify(nextSession.key)}, 0)`);
    const sessionCommit = await evaluate(`new Promise((done, reject) => { let n=0; const timer=setInterval(() => {
      if (window.firstCommit) { clearInterval(timer); done(window.firstCommit); }
      else if (++n > 100) { clearInterval(timer); reject(Error('Session switch did not capture its first commit')); }
    }, 25) })`);
    expect(sessionCommit.targetSession).toBe(nextSession.key);
    expect(sessionCommit.controllerSession).toBe(submitted.sessionId);
    expect(sessionCommit.inputValue).toBe("");
    expect(sessionCommit.inputDisabled || sessionCommit.inputReadOnly).toBe(true);
    expect(sessionCommit.sendDisabled).toBe(true);
    expect(sessionCommit.returnDisabled).toBe(true);
    expect(sessionCommit.body).not.toContain(prompt);
    expect(sessionCommit.body).not.toContain(retainedDraft);
    expect(sessionCommit.afterEvents.draft).toBe(retainedDraft);
    expect(sessionCommit.afterEvents.phase).toBe("ready");
    await evaluate(`new Promise((done, reject) => { let n=0; const timer=setInterval(() => {
      const state = window.snapshot();
      if (state.sessionId === ${JSON.stringify(nextSession.key)} && state.phase === 'ready') { clearInterval(timer); done(true); }
      else if (++n > 200) { clearInterval(timer); reject(Error('The selected owned session did not become ready: ' + JSON.stringify(state))); }
    }, 50) })`);

    const ownerDraft = "This draft belongs to the first owner.";
    await evaluate(`(() => {
      const input = document.getElementById('gideon-message-composer');
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value').set;
      setter.call(input, ${JSON.stringify(ownerDraft)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    expect(await evaluate("window.snapshot().draft")).toBe(ownerDraft);
    await conversationServer.rotateOwner("conversation-owner-two");
    await evaluate("window.captureNextCommit(true)");
    await evaluate("window.switchToSecondOwner()");
    const ownerCommit = await evaluate(`new Promise((done, reject) => { let n=0; const timer=setInterval(() => {
      if (window.firstCommit) { clearInterval(timer); done(window.firstCommit); }
      else if (++n > 200) { clearInterval(timer); reject(Error('Owner switch did not capture its first commit')); }
    }, 25) })`);
    expect(ownerCommit.targetOwner).toBe("conversation-owner-two");
    expect(ownerCommit.controllerOwner).toBe("conversation-owner");
    expect(ownerCommit.inputValue).toBe("");
    expect(ownerCommit.inputDisabled || ownerCommit.inputReadOnly).toBe(true);
    expect(ownerCommit.sendDisabled).toBe(true);
    expect(ownerCommit.returnDisabled).toBe(true);
    expect(ownerCommit.body).not.toContain(ownerDraft);
    expect(ownerCommit.body).not.toContain(prompt);
    expect(ownerCommit.afterEvents.draft).toBe(ownerDraft);
    expect(ownerCommit.afterEvents.phase).toBe("ready");
    await evaluate(`new Promise((done, reject) => { let n=0; const timer=setInterval(() => {
      const state = window.snapshot();
      const input = document.getElementById('gideon-message-composer');
      if (state.scope?.cacheKey === window.expectedOwnerCacheKey
        && state.sessionId === null && input && !input.disabled) { clearInterval(timer); done(true); }
      else if (++n > 200) { clearInterval(timer); reject(Error('The new owner context did not become ready: ' + JSON.stringify(state))); }
    }, 50) })`);
    await evaluate("window.disposeController()");
  }, 60000);
});
