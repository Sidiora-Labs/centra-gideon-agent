import { execFile } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer as createNetServer } from "node:net";
import { join } from "node:path";
import { promisify } from "node:util";
import { afterAll, describe, expect, it } from "vitest";
import { startBrowserHarness, startViteEntryServer, type BrowserHarness } from "../../../test-support/browserHarness";
import { startNativeServer, type NativeServer } from "../../../test-support/nativeServer";
import { assistantConsoleReturnHref } from "../../../../console/src/app/shell/assistantRouteBridge";
import { consoleReturnHref, registerAssistantServiceWorker } from "./entry.web";

const root = process.cwd().replace(/\/apps\/assistant$/, "");
const execute = promisify(execFile);
let vite: Awaited<ReturnType<typeof startViteEntryServer>> | undefined;
let browser: BrowserHarness | undefined;
let native: NativeServer | undefined;
let entryDirectory: string | undefined;
let trustedAssetsDirectory: string | undefined;

afterAll(async () => {
  await browser?.close();
  await native?.stop();
  if (vite) await vite.close();
  if (entryDirectory) await rm(entryDirectory, { recursive: true, force: true });
  if (trustedAssetsDirectory) await rm(trustedAssetsDirectory, { recursive: true, force: true });
});

async function availablePort(): Promise<number> {
  const server = createNetServer();
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise<void>(resolve => server.close(() => resolve()));
  return port;
}

describe("assistant web entry", () => {
  it("skips root worker registration when disabled", async () => {
    expect(await registerAssistantServiceWorker(false)).toBeNull();
  });

  it("keeps assistant and console return links equivalent without leaking private query fields", () => {
    const context = { destination: "chat" as const, sessionId: "session/one", placement: {
      id: "console", query: { tab: "history", draft: "private", access_token: "private" },
    } };
    expect(consoleReturnHref(context)).toBe(assistantConsoleReturnHref(context));
    expect(consoleReturnHref(context)).toBe("/#/chat/session%2Fone?tab=history");
  });

  it("opens an owned session after sign-in, survives reload and Back, and recovers missing destinations", async () => {
    const port = await availablePort();
    const origin = `http://127.0.0.1:${port}`;
    native = await startNativeServer({
      script: join(root, "apps/assistant/test-support/module_server.py"),
      origin,
      repositoryRoot: root,
    });
    trustedAssetsDirectory = await mkdtemp(join(root, "apps/assistant/.entry-assets-"));
    await execute(process.execPath, [join(root, "apps/assistant/tooling/buildTrustedWebAssets.mjs"), "--out-dir", trustedAssetsDirectory], {
      cwd: join(root, "apps/assistant"),
    });
    entryDirectory = await mkdtemp(join(root, "apps/assistant/.entry-browser-"));
    const entryFile = join(entryDirectory, "entry.tsx");
    await writeFile(entryFile, `import { createRoot } from 'react-dom/client';
import App from '/App.tsx';
createRoot(document.getElementById('root')!).render(<App />);`, "utf8");
    vite = await startViteEntryServer({
      root: join(root, "apps/assistant"),
      port,
      entryFile,
      apiOrigin: native.apiOrigin,
      staticAssetsDirectory: trustedAssetsDirectory,
    });
    browser = await startBrowserHarness({ windowSize: { width: 1280, height: 900 } });
    await browser.navigate(`${origin}/assistant/chat?v=1`);
    await browser.waitFor("document.querySelector('#gideon-password')", "native owner sign-in form");
    await browser.evaluate(`(()=>{
      const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};
      set('gideon-username','module-owner');set('gideon-password','correct-horse-battery-staple');document.querySelector('form').requestSubmit();return true
    })()`);
    await browser.waitFor("document.querySelector('[data-gideon-assistant]')?.textContent.includes('Signed in as module-owner') && document.querySelector('[aria-label=\"Message Gideon\"]')", "authenticated assistant workspace");
    const created = await browser.evaluate<{ key: string }>(`fetch('/api/chat/sessions',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'},body:'{}'}).then(r=>r.json())`);
    expect(typeof created.key).toBe("string");
    const sessionUrl = `${origin}/assistant/chat?v=1&view=detail&recordKind=chat_session&recordId=${encodeURIComponent(created.key)}&from=apps&fromPlacement=console&fromq.tab=details`;
    await browser.evaluate(`fetch('/api/auth/logout',{method:'POST',credentials:'same-origin',headers:{'X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'}}).then(()=>true)`);
    await browser.navigate(sessionUrl);
    await browser.waitFor("document.querySelector('#gideon-password')", "signed-out deep link");
    expect(await browser.evaluate<string>("location.href")).toBe(sessionUrl);
    await browser.evaluate(`(()=>{
      const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};
      set('gideon-username','module-owner');set('gideon-password','correct-horse-battery-staple');document.querySelector('form').requestSubmit();return true
    })()`);
    const ownedConversation = `location.href === ${JSON.stringify(sessionUrl)} && document.querySelector('[data-gideon-assistant]')?.textContent.includes('Signed in as module-owner') && document.querySelector('[aria-label="Gideon conversation"]') && document.querySelector('[aria-label="Message Gideon"]')`;
    await browser.waitFor(ownedConversation, "owned session conversation");
    expect(await browser.evaluate<string>("location.href")).toBe(sessionUrl);
    await browser.command("Page.reload", { ignoreCache: true }).catch((error: unknown) => {
      if (!(error instanceof Error) || !error.message.includes("Inspected target navigated or closed")) throw error;
    });
    await browser.waitFor(`performance.getEntriesByType('navigation')[0]?.type === 'reload' && ${ownedConversation}`, "session after reload");
    const taskId = String(native.startup.task_id);
    await browser.navigate(`${origin}/assistant/activity?v=1&view=detail&placement=tasks&recordKind=task&recordId=${encodeURIComponent(taskId)}&from=chat`);
    await browser.waitFor("document.querySelector('[data-gideon-module=\"tasks\"]')?.innerText.includes('Native task detail fixture')", "real seeded TaskDetail workspace", 60000);
    const trustedAssets = await browser.evaluate<{ workerStatus: number; workerType: string; workerBytes: number; missingStatus: number }>(`(async()=>{
      const worker=await fetch('/assistant/assets/workers/gideon-monaco-editor.worker.js');
      const workerBytes=(await worker.arrayBuffer()).byteLength;
      const missing=await fetch('/assistant/assets/workers/absent-gideon-worker.js');
      return {workerStatus:worker.status,workerType:worker.headers.get('content-type')||'',workerBytes,missingStatus:missing.status}
    })()`);
    expect(trustedAssets.workerStatus).toBe(200);
    expect(trustedAssets.workerType).toContain("javascript");
    expect(trustedAssets.workerBytes).toBeGreaterThan(0);
    expect(trustedAssets.missingStatus).toBe(404);
    await browser.evaluate("history.back()");
    await browser.waitFor(ownedConversation, "Back to the owned session");
    await browser.evaluate(`Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Return to previous workspace')?.click()`);
    await browser.waitFor(`location.pathname === '/assistant/apps' && new URLSearchParams(location.search).get('placement') === 'console' && new URLSearchParams(location.search).get('q.tab') === 'details' && document.querySelector('main.gideon-workspace-frame')?.dataset.workspaceState === 'error' && document.querySelector('[role="alert"]')?.textContent === 'Try again to check access.'`, "typed return context with unavailable console destination");
    await browser.navigate(`${origin}/assistant/chat?v=1&view=detail&recordKind=session&recordId=absent-session`);
    await browser.waitFor("document.body.innerText.includes('The requested item may have been removed.')", "missing session recovery");
    await browser.evaluate(`Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Go to Chat')?.click()`);
    await browser.waitFor("location.pathname === '/assistant/chat' && document.querySelector('[data-gideon-assistant]')?.textContent.includes('Signed in as module-owner') && document.querySelector('[aria-label=\"Message Gideon\"]')", "return to Chat");
    await browser.navigate(`${origin}/assistant/apps?v=1&view=detail&recordKind=app&recordId=app-1`);
    await browser.waitFor(`document.querySelector('main.gideon-workspace-frame')?.dataset.workspaceState === 'error' && document.querySelector('[role="alert"]')?.textContent === 'Try again to check access.' && Array.from(document.querySelectorAll('button')).some(button=>button.textContent?.trim()==='Retry')`, "unsupported application destination unavailable recovery");
  }, 90000);
});
