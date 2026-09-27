import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { createServer as createNetServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";
// Node's strip-types test runner requires the explicit TypeScript extension.
// @ts-expect-error TS5097: this test is executed directly from TypeScript by Node.
import { startBrowserHarness, startViteEntryServer } from "./browserHarness.ts";
// @ts-expect-error TS5097: this test is executed directly from TypeScript by Node.
import { startNativeServer, type NativeServer } from "./nativeServer.ts";

async function availablePort(): Promise<number> {
  const server = createNetServer();
  await new Promise<void>(resolveListen => server.listen(0, "127.0.0.1", resolveListen));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise<void>(resolveClose => server.close(() => resolveClose()));
  return port;
}

test("waits for the real Chromium process to close before removing its owned profile", async () => {
  const browser = await startBrowserHarness({ chromium: process.env.CHROMIUM_BIN || "chromium" });
  let processClosed = false;
  browser.child.once("close", () => { processClosed = true; });
  await browser.close();
  assert.equal(processClosed, true);
});

test("concurrent Vite journeys isolate optimizer caches and clean them up", async () => {
  const root = resolve(import.meta.dirname, "..");
  const entryFile = join(root, "src/shared/shell/WorkspaceFrame.web.tsx");
  const [firstPort, secondPort] = await Promise.all([availablePort(), availablePort()]);
  const [first, second] = await Promise.all([
    startViteEntryServer({ root, port: firstPort, entryFile, apiOrigin: "http://127.0.0.1:9" }),
    startViteEntryServer({ root, port: secondPort, entryFile, apiOrigin: "http://127.0.0.1:9" }),
  ]);
  const profileDirectory = await mkdtemp(join(tmpdir(), "gideon-assistant-cache-browser-"));
  const browser = await startBrowserHarness({ profileDirectory });
  try {
    const firstCache = first.config.cacheDir;
    const secondCache = second.config.cacheDir;
    assert.notEqual(firstCache, secondCache);
    assert.ok(firstCache.startsWith(tmpdir()));
    assert.ok(secondCache.startsWith(tmpdir()));

    const firstOrigin = `http://127.0.0.1:${firstPort}`;
    await browser.navigate(`${firstOrigin}/assistant`);
    await browser.waitFor("document.readyState === 'complete'", "first Vite entry document");
    const firstExport = await browser.evaluate<string>(
      `import(${JSON.stringify(`${firstOrigin}/@fs${entryFile}?journey=first`)}).then(module => typeof module.WorkspaceFrame)`,
    );
    assert.equal(firstExport, "function");

    const secondOrigin = `http://127.0.0.1:${secondPort}`;
    await browser.navigate(`${secondOrigin}/assistant`);
    await browser.waitFor("document.readyState === 'complete'", "second Vite entry document");
    const secondExport = await browser.evaluate<string>(
      `import(${JSON.stringify(`${secondOrigin}/@fs${entryFile}?journey=second`)}).then(module => typeof module.WorkspaceFrame)`,
    );
    assert.equal(secondExport, "function");

    await first.close();
    assert.ok(first.httpServer);
    assert.equal(first.httpServer.listening, false);
    const afterCloseExport = await browser.evaluate<string>(
      `import(${JSON.stringify(`${secondOrigin}/@fs${entryFile}?journey=after-first-close`)}).then(module => typeof module.WorkspaceFrame)`,
    );
    assert.equal(afterCloseExport, "function");
    assert.ok(second.httpServer);
    assert.equal(second.httpServer.listening, true);

    await assert.rejects(readdir(firstCache));
    await assert.doesNotReject(readdir(secondCache));
  } finally {
    await browser.close();
    await Promise.all([first.close(), second.close()]);
    await rm(profileDirectory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
  await assert.rejects(readdir(second.config.cacheDir));

  const heldPort = await availablePort();
  const blocker = createNetServer();
  await new Promise<void>(resolveListen => blocker.listen(heldPort, "127.0.0.1", resolveListen));
  const cachePrefix = `gideon-vite-cache-${process.pid}-`;
  try {
    await assert.rejects(startViteEntryServer({
      root,
      port: heldPort,
      entryFile,
      apiOrigin: "http://127.0.0.1:9",
    }));
  } finally {
    await new Promise<void>(resolveClose => blocker.close(() => resolveClose()));
  }
  assert.deepEqual((await readdir(tmpdir())).filter(name => name.startsWith(cachePrefix)), []);
});

test("the real App is dependency-optimized before authentication and stays error-free after login", async t => {
  const repositoryRoot = resolve(import.meta.dirname, "../../..");
  const root = join(repositoryRoot, "apps/assistant");
  const directory = await mkdtemp(join(root, ".browser-harness-app-"));
  const entryFile = join(directory, "entry.tsx");
  await writeFile(entryFile, `import { createRoot } from 'react-dom/client';
import App from '/App.tsx';
createRoot(document.getElementById('root')!).render(<App />);`, "utf8");
  const port = await availablePort();
  const origin = `http://127.0.0.1:${port}`;
  let native: NativeServer | undefined;
  let vite: Awaited<ReturnType<typeof startViteEntryServer>> | undefined;
  let browser: Awaited<ReturnType<typeof startBrowserHarness>> | undefined;
  try {
    native = await startNativeServer({
      script: join(root, "test-support/module_server.py"),
      origin,
      repositoryRoot,
      python: process.env.GIDEON_TEST_PYTHON || "python3",
    });
    vite = await startViteEntryServer({
      root,
      port,
      entryFile,
      apiOrigin: native.apiOrigin,
    });
    browser = await startBrowserHarness({
      chromium: process.env.CHROMIUM_BIN || "chromium",
      profileDirectory: join(directory, "chromium"),
      windowSize: { width: 1280, height: 900 },
    });
    await browser.navigate(`${origin}/assistant/chat?v=1`);
    await browser.waitFor("document.querySelector('#gideon-password')", "real App sign-in form");

    const metadataPath = join(vite.config.cacheDir, "deps", "_metadata.json");
    const beforeLoginAt = await browser.evaluate<number>("Date.now()");
    const beforeLoginDiagnostics = browser.diagnostics();
    const beforeLoginMetadata = JSON.parse(await readFile(metadataPath, "utf8")) as {
      browserHash?: string;
      optimized?: Record<string, unknown>;
    };
    assert.ok(beforeLoginMetadata.browserHash);
    assert.ok(beforeLoginMetadata.optimized?.["react-native-web"]);
    assert.ok(beforeLoginMetadata.optimized?.["react-dom"]);
    assert.ok(beforeLoginMetadata.optimized?.["react-dom/client"]);
    assert.ok(beforeLoginMetadata.optimized?.["expo-status-bar"]);

    await browser.evaluate(`(()=>{
      const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};
      set('gideon-username','module-owner');set('gideon-password','correct-horse-battery-staple');document.querySelector('form').requestSubmit();return true
    })()`);
    await browser.waitFor("document.querySelector('#gideon-message-composer')", "authenticated real App composer");

    const afterLoginAt = await browser.evaluate<number>("Date.now()");
    const afterLoginDiagnostics = browser.diagnostics();
    const afterLoginMetadata = JSON.parse(await readFile(metadataPath, "utf8")) as {
      browserHash?: string;
      optimized?: Record<string, unknown>;
    };
    assert.equal(afterLoginMetadata.browserHash, beforeLoginMetadata.browserHash);
    assert.deepEqual(afterLoginMetadata.optimized, beforeLoginMetadata.optimized);
    assert.doesNotMatch(
      [...beforeLoginDiagnostics, ...afterLoginDiagnostics].join("\n"),
      /invalid hook call|dispatcher.*null|use(?:State|Context).*null|properties of null.*use(?:State|Context)/i,
    );
    t.diagnostic(JSON.stringify({
      authenticatedAppWarmup: {
        beforeLoginAt,
        afterLoginAt,
        preLoginDiagnostics: beforeLoginDiagnostics,
        postLoginDiagnostics: afterLoginDiagnostics,
        optimizedDependencies: Object.keys(beforeLoginMetadata.optimized ?? {}).sort(),
        optimizerBrowserHashStableAcrossLogin: true,
      },
    }));
  } finally {
    await browser?.close();
    await vite?.close();
    await native?.stop();
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
});
