import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, describe, expect, it } from "vitest";
import { startBrowserHarness, type BrowserHarness } from "../../../test-support/browserHarness";
import { startNativeServer, type NativeServer } from "../../../test-support/nativeServer";

const root = process.cwd().replace(/\/apps\/assistant$/, "");
let vite: ViteDevServer | undefined;
let browser: BrowserHarness | undefined;
let native: NativeServer | undefined;
let entryDirectory: string | undefined;

afterAll(async () => {
  await browser?.close();
  await native?.stop();
  if (vite) await vite.close();
  if (entryDirectory) await rm(entryDirectory, { recursive: true, force: true });
});

describe("real browser authentication bootstrap", () => {
  it("authenticates through the native API and establishes the owner-scoped bootstrap", async () => {
    // Reserve a concrete port first because auth_server binds its CORS origin to it.
    const net = await import("node:net");
    const probe = net.createServer();
    await new Promise<void>(resolve => probe.listen(0, "127.0.0.1", resolve));
    const port = (probe.address() as { port: number }).port;
    await new Promise<void>(resolve => probe.close(() => resolve()));
    const origin = `http://127.0.0.1:${port}`;

    native = await startNativeServer({
      script: `${root}/apps/assistant/test-support/auth_server.py`,
      origin,
      repositoryRoot: root,
    });
    entryDirectory = await mkdtemp(join(tmpdir(), "gideon-auth-entry-"));
    const entryFile = `${entryDirectory}/auth-entry.tsx`;
    await writeFile(entryFile, `import * as React from 'react';
import { createRoot } from 'react-dom/client';
import { AssistantBootstrapProvider, useAssistantBootstrap } from '/src/shared/bootstrap.web.tsx';
function Ready() {
  const bootstrap = useAssistantBootstrap();
  React.useEffect(() => { window.__bootstrap = bootstrap; }, [bootstrap]);
  return <p id="owner-ready">{bootstrap.state.owner?.user}</p>;
}
createRoot(document.getElementById('root')!).render(<AssistantBootstrapProvider><Ready /></AssistantBootstrapProvider>);`, "utf8");
    vite = await createServer({
      configFile: false,
      root: `${root}/apps/assistant`,
      esbuild: { jsx: "automatic" },
      resolve: { dedupe: ["react", "react-dom"] },
      plugins: [{
        name: "gideon-real-auth-entry",
        configureServer(viteServer) {
          viteServer.middlewares.use("/assistant", (_request, response) => {
            response.setHeader("Content-Type", "text/html; charset=utf-8");
            response.end(`<!doctype html><html><body><div id="root"></div><script type="module" src="/@fs${entryFile}"></script></body></html>`);
          });
        },
      }],
      server: { host: "127.0.0.1", port, strictPort: true, fs: { allow: [root, entryDirectory] }, proxy: { "/api": native.apiOrigin } },
    });
    await vite.listen();
    browser = await startBrowserHarness({ windowSize: { width: 1280, height: 900 } });
    await browser.navigate(`${origin}/assistant`);
    await browser.waitFor("document.querySelector('#gideon-password')", "real owner sign-in form");
    await browser.evaluate(`(()=>{
      const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};
      set('gideon-username','owner-a');set('gideon-password','correct-horse-battery-staple');document.querySelector('form').requestSubmit();return true
    })()`);
    await browser.waitFor(`document.querySelector('#owner-ready')?.textContent === 'owner-a'`, "authenticated owner bootstrap");
    const session = await browser.evaluate<{ user: string; phase: string; ownerId: string; runtimeOrigin: string }>(`(()=>{const state=window.__bootstrap?.state;return {user:state?.owner?.user,phase:state?.phase,ownerId:state?.scope?.ownerId,runtimeOrigin:state?.scope?.runtimeOrigin}})()`);
    expect(session).toEqual({ user: "owner-a", phase: "ready", ownerId: "owner-a", runtimeOrigin: origin });
  }, 60000);
});
