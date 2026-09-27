import assert from "node:assert/strict";
import { readdir } from "node:fs/promises";
import { createServer as createNetServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";
// Node's strip-types test runner requires the explicit TypeScript extension.
// @ts-expect-error TS5097: this test is executed directly from TypeScript by Node.
import { startBrowserHarness, startViteEntryServer } from "./browserHarness.ts";

async function availablePort(): Promise<number> {
  const server = createNetServer();
  await new Promise<void>(resolveListen => server.listen(0, "127.0.0.1", resolveListen));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise<void>(resolveClose => server.close(() => resolveClose()));
  return port;
}

test("concurrent Vite journeys isolate optimizer caches and clean them up", async () => {
  const root = resolve(import.meta.dirname, "..");
  const entryFile = join(root, "src/shared/shell/WorkspaceFrame.web.tsx");
  const [firstPort, secondPort] = await Promise.all([availablePort(), availablePort()]);
  const [first, second] = await Promise.all([
    startViteEntryServer({ root, port: firstPort, entryFile, apiOrigin: "http://127.0.0.1:9" }),
    startViteEntryServer({ root, port: secondPort, entryFile, apiOrigin: "http://127.0.0.1:9" }),
  ]);
  const browser = await startBrowserHarness();
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
