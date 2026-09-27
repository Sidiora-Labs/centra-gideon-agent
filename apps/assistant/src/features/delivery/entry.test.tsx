import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer as createNetServer } from "node:net";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { startBrowserHarness, startViteEntryServer, type BrowserHarness } from "../../../test-support/browserHarness";
import { startNativeServer, type NativeServer } from "../../../test-support/nativeServer";
import { assistantConsoleReturnHref } from "../../../../console/src/app/shell/assistantRouteBridge";
import { consoleReturnHref, registerAssistantServiceWorker } from "./entry.web";

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
    await browser.waitFor("document.body.innerText.includes('0 messages in this conversation.')", "owned session detail");
    expect(await browser.evaluate<string>("location.href")).toBe(sessionUrl);
    await browser.command("Page.reload", { ignoreCache: true });
    await browser.waitFor("performance.getEntriesByType('navigation')[0]?.type === 'reload' && document.body.innerText.includes('0 messages in this conversation.')", "session after reload");
    await browser.evaluate(`document.querySelector('[aria-label="Ideas"]')?.click()`);
    await browser.waitFor("location.pathname === '/assistant/ideas' && document.querySelector('#personal-ideas-title')?.textContent === 'Ideas' && document.querySelector('#personal-idea-draft')", "native Ideas workspace");
    await browser.evaluate("history.back()");
    await browser.waitFor(`location.href === ${JSON.stringify(sessionUrl)} && document.body.innerText.includes('0 messages in this conversation.')`, "Back to the owned session");
    await browser.evaluate(`Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Return to previous workspace')?.click()`);
    await browser.waitFor("location.hash === '#/apps?tab=details'", "console return location");
    await browser.navigate(`${origin}/assistant/chat?v=1&view=detail&recordKind=session&recordId=absent-session`);
    await browser.waitFor("document.body.innerText.includes('The requested item may have been removed.')", "missing session recovery");
    await browser.evaluate(`Array.from(document.querySelectorAll('button')).find(button=>button.innerText==='Go to Chat')?.click()`);
    await browser.waitFor("location.pathname === '/assistant/chat' && document.querySelector('[data-gideon-assistant]')?.textContent.includes('Signed in as module-owner') && document.querySelector('[aria-label=\"Message Gideon\"]')", "return to Chat");
    await browser.navigate(`${origin}/assistant/apps?v=1&view=detail&recordKind=app&recordId=app-1`);
    await browser.waitFor("document.body.innerText.includes('This item cannot be checked') && document.body.innerText.includes('Retry destination')", "unavailable module recovery");
  }, 90000);
});
