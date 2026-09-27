import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer as createNetServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { createServer, type ViteDevServer } from "vite";

type PendingCommand = { resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void; timeout: ReturnType<typeof setTimeout> };

export type BrowserHarness = Readonly<{
  child: ChildProcess;
  command: (method: string, params?: Record<string, unknown>) => Promise<Record<string, unknown>>;
  evaluate: <T = unknown>(expression: string) => Promise<T>;
  navigate: (url: string) => Promise<void>;
  waitFor: (expression: string, label?: string, timeoutMs?: number) => Promise<void>;
  diagnostics: () => readonly string[];
  close: () => Promise<void>;
}>;

export async function startViteEntryServer(options: {
  root: string;
  port: number;
  entryFile: string;
  apiOrigin: string;
  route?: string;
}): Promise<ViteDevServer> {
  const root = resolve(options.root);
  const entryFile = resolve(options.entryFile);
  const route = options.route ?? "/assistant";
  const cacheDir = await mkdtemp(join(tmpdir(), `gideon-vite-cache-${process.pid}-`));
  let server: ViteDevServer | undefined;
  try {
    server = await createServer({
      configFile: false,
      root,
      cacheDir,
      resolve: {
        alias: [
          { find: /^react-native$/, replacement: "react-native-web" },
          { find: "expo-status-bar", replacement: resolve(root, "node_modules/expo-status-bar/src/StatusBar.web.ts") },
        ],
        extensions: [".web.tsx", ".web.ts", ".web.js", ".tsx", ".ts", ".jsx", ".js", ".json"],
        dedupe: ["react", "react-dom"],
      },
      esbuild: { jsx: "automatic" },
      optimizeDeps: {
        entries: [relative(root, entryFile)],
        include: [
          "expo-status-bar",
          "lucide-react-native",
          "react",
          "react-dom",
          "react-dom/client",
          "react-native-safe-area-context",
          "react-native-web",
          "react/jsx-dev-runtime",
        ],
        esbuildOptions: { resolveExtensions: [".web.tsx", ".web.ts", ".web.js", ".tsx", ".ts", ".jsx", ".js", ".json"] },
      },
      plugins: [{
        name: "gideon-actual-tsx-entry",
        configureServer(viteServer) {
          viteServer.middlewares.use(route, (_request, response) => {
            response.setHeader("Content-Type", "text/html; charset=utf-8");
            response.end(`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@fs${entryFile}"></script></body></html>`);
          });
        },
      }],
      server: {
        host: "127.0.0.1",
        port: options.port,
        strictPort: true,
        fs: { allow: [root, dirname(entryFile)] },
        proxy: { "/api": { target: options.apiOrigin, ws: true } },
      },
    });
    const viteServer = server;
    const closeViteServer = viteServer.close.bind(viteServer);
    let closed = false;
    viteServer.close = async () => {
      if (closed) return;
      closed = true;
      try {
        await closeViteServer();
      } finally {
        await rm(cacheDir, { recursive: true, force: true });
      }
    };
    await viteServer.listen();
    return viteServer;
  } catch (error) {
    await server?.close().catch(() => undefined);
    await rm(cacheDir, { recursive: true, force: true });
    throw error;
  }
}

async function availablePort(): Promise<number> {
  const server = createNetServer();
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise<void>(resolve => server.close(() => resolve()));
  return port;
}

export async function startBrowserHarness(options: {
  chromium?: string;
  profileDirectory?: string;
  windowSize?: { width: number; height: number };
} = {}): Promise<BrowserHarness> {
  const profileDirectory = options.profileDirectory ?? await mkdtemp(join(tmpdir(), "gideon-assistant-chromium-"));
  const ownsProfile = options.profileDirectory === undefined;
  const debugPort = await availablePort();
  const child = spawn(options.chromium || process.env.CHROMIUM_BIN || "chromium", [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--no-first-run",
    `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profileDirectory}`, "about:blank",
  ], {
    stdio: ["ignore", "ignore", "pipe"],
    env: Object.fromEntries(Object.entries(process.env).filter(([key]) => key !== "OPENAI_API_KEY")),
  });
  let browserErrors = "";
  child.stderr.on("data", chunk => { browserErrors = (browserErrors + String(chunk)).slice(-3000); });
  let target: { webSocketDebuggerUrl: string } | undefined;
  for (let attempt = 0; attempt < 120 && !target; attempt += 1) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json() as
        Array<{ type: string; webSocketDebuggerUrl: string }>;
      target = targets.find(item => item.type === "page");
    } catch { await new Promise(resolve => setTimeout(resolve, 100)); }
    if (child.exitCode !== null) break;
  }
  if (!target) {
    child.kill("SIGTERM");
    if (ownsProfile) await rm(profileDirectory, { recursive: true, force: true });
    throw new Error(`Chromium debugger unavailable: ${browserErrors}`);
  }
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  try {
    await new Promise<void>((resolve, reject) => {
      socket.addEventListener("open", () => resolve(), { once: true });
      socket.addEventListener("error", () => reject(new Error(`Chromium debugger connection failed: ${browserErrors}`)), { once: true });
    });
  } catch (error) {
    socket.close();
    child.kill("SIGTERM");
    if (ownsProfile) await rm(profileDirectory, { recursive: true, force: true });
    throw error;
  }

  let sequence = 0;
  let closed = false;
  const pending = new Map<number, PendingCommand>();
  const firstDiagnostics = new Map<string, string>();
  socket.addEventListener("message", event => {
    const message = JSON.parse(String(event.data)) as {
      id?: number;
      method?: string;
      params?: Record<string, unknown>;
      result?: Record<string, unknown>;
      error?: { message: string };
    };
    if (message.method) {
      let category = "";
      let description = "";
      if (message.method === "Runtime.exceptionThrown") {
        category = "exception";
        description = JSON.stringify(message.params?.exceptionDetails ?? message.params);
      } else if (message.method === "Runtime.consoleAPICalled") {
        category = "console";
        description = JSON.stringify(message.params?.args ?? message.params);
      } else if (message.method === "Log.entryAdded") {
        category = "console";
        description = JSON.stringify(message.params?.entry ?? message.params);
      } else if (message.method === "Network.loadingFailed") {
        category = "network";
        description = JSON.stringify(message.params);
      }
      if (category && !firstDiagnostics.has(category)) firstDiagnostics.set(category, `${category}: ${description}`);
    }
    if (message.id === undefined) return;
    const waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    clearTimeout(waiter.timeout);
    if (message.error) waiter.reject(new Error(message.error.message));
    else waiter.resolve(message.result ?? {});
  });
  socket.addEventListener("close", () => {
    for (const waiter of pending.values()) {
      clearTimeout(waiter.timeout);
      waiter.reject(new Error(`Chromium debugger socket closed; ${[...firstDiagnostics.values()].join(" | ") || browserErrors || "no browser diagnostics"}`));
    }
    pending.clear();
  });

  const command = (method: string, params: Record<string, unknown> = {}) => new Promise<Record<string, unknown>>((resolve, reject) => {
    if (closed || socket.readyState !== WebSocket.OPEN) { reject(new Error("Chromium debugger is closed")); return; }
    const id = ++sequence;
    const timeout = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`Chromium command timed out (${method}); ${[...firstDiagnostics.values()].join(" | ") || "no browser diagnostics"}`));
    }, 30000);
    pending.set(id, { resolve, reject, timeout });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async <T,>(expression: string): Promise<T> => {
    const result = await command("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }) as {
      result?: { value?: T };
      exceptionDetails?: { text?: string; exception?: { description?: string } };
    };
    if (result.exceptionDetails) {
      throw new Error(`${result.exceptionDetails.exception?.description || result.exceptionDetails.text || "Browser evaluation failed"}; ${[...firstDiagnostics.values()].join(" | ") || "no browser diagnostics"}`);
    }
    return result.result?.value as T;
  };
  const navigate = async (url: string) => { await command("Page.navigate", { url }); };
  const waitFor = async (expression: string, label = expression, timeoutMs = 20000) => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (await evaluate<boolean>(`Boolean(${expression})`)) return;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    const details = await evaluate<string>("document.body?.innerText || ''").catch(error => String(error));
    throw new Error(`Timed out waiting for ${label}; URL=${await evaluate<string>("location.href")}; body=${details}; ${[...firstDiagnostics.values()].join(" | ") || "no browser diagnostics"}`);
  };

  try {
    await command("Page.enable");
    await command("Runtime.enable");
    await command("Log.enable");
    await command("Network.enable");
    if (options.windowSize) {
      await command("Emulation.setDeviceMetricsOverride", { ...options.windowSize, deviceScaleFactor: 1, mobile: options.windowSize.width < 800 });
    }
  } catch (error) {
    socket.close();
    child.kill("SIGTERM");
    if (ownsProfile) await rm(profileDirectory, { recursive: true, force: true });
    throw error;
  }

  return {
    child,
    command,
    evaluate,
    navigate,
    waitFor,
    diagnostics: () => [...firstDiagnostics.values()],
    close: async () => {
      if (closed) return;
      closed = true;
      socket.close();
      child.kill("SIGTERM");
      await new Promise<void>(resolve => {
        if (child.exitCode !== null || child.signalCode !== null) { resolve(); return; }
        child.once("exit", () => resolve());
        setTimeout(resolve, 5000);
      });
      if (ownsProfile) await rm(profileDirectory, { recursive: true, force: true });
    },
  };
}
