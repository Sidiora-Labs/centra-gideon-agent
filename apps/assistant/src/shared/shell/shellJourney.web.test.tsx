import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { startBrowserHarness, startViteEntryServer, type BrowserHarness } from "../../../test-support/browserHarness";
import { startNativeServer, type NativeServer } from "../../../test-support/nativeServer";

const root = process.cwd().replace(/\/apps\/assistant$/, "");
let vite: Awaited<ReturnType<typeof startViteEntryServer>> | undefined;
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
    entryDirectory = await mkdtemp(join(root, "apps/assistant/.shell-journey-entry-"));
    const entryFile = `${entryDirectory}/auth-entry.tsx`;
    await writeFile(entryFile, `import { createRoot } from 'react-dom/client';
import App from '/App.tsx';
createRoot(document.getElementById('root')!).render(<App />);`, "utf8");
    vite = await startViteEntryServer({
      root: `${root}/apps/assistant`,
      port,
      entryFile,
      apiOrigin: native.apiOrigin,
    });
    browser = await startBrowserHarness({ windowSize: { width: 1280, height: 900 } });
    await browser.navigate(`${origin}/assistant`);
    await browser.waitFor("document.querySelector('#gideon-password')", "real owner sign-in form");
    await browser.evaluate(`(()=>{
      const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};
      set('gideon-username','owner-a');set('gideon-password','correct-horse-battery-staple');document.querySelector('form').requestSubmit();return true
    })()`);
    await browser.waitFor(`document.querySelector('[data-gideon-assistant]')?.textContent.includes('Signed in as owner-a')`, "authenticated Gideon App");
    const shell = await browser.evaluate<{ roots: number; owner: string }>(`({roots:document.querySelectorAll('#root').length,owner:document.querySelector('[data-gideon-assistant]')?.textContent||''})`);
    expect(shell.roots).toBe(1);
    expect(shell.owner).toContain("Signed in as owner-a");
  }, 60000);
});
