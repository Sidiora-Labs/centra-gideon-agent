import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer as createNetServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { startBrowserHarness, startViteEntryServer } from "../../../test-support/browserHarness";
import { startNativeServer, type NativeServer } from "../../../test-support/nativeServer";

const repositoryRoot = resolve(process.cwd(), "../..");
let native: NativeServer | undefined;
let vite: Awaited<ReturnType<typeof startViteEntryServer>> | undefined;
let browser: Awaited<ReturnType<typeof startBrowserHarness>> | undefined;
let temporary: string | undefined;

async function availablePort(): Promise<number> {
  const server = createNetServer();
  await new Promise<void>(done => server.listen(0, "127.0.0.1", done));
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  await new Promise<void>(done => server.close(() => done()));
  return port;
}

async function capture(name: string): Promise<void> {
  const directory = process.env.GIDEON_LIBRARY_EVIDENCE_DIR;
  if (!directory || !browser) return;
  await mkdir(directory, { recursive: true });
  const screenshot = await browser.command("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  const data = screenshot.data;
  if (typeof data !== "string") throw new Error(`Chromium did not return ${name} screenshot data.`);
  await writeFile(join(directory, name), Buffer.from(data, "base64"));
}

function makePdf(): Uint8Array {
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    (() => { const stream = "BT /F1 12 Tf 24 100 Td (A native reader passage.) Tj ET\n"; return `<< /Length ${new TextEncoder().encode(stream).length} >>\nstream\n${stream}endstream`; })(),
  ];
  let pdf = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(new TextEncoder().encode(pdf).length);
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xrefOffset = new TextEncoder().encode(pdf).length;
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const offset of offsets.slice(1)) pdf += `${String(offset).padStart(10, "0")} 00000 n \n`;
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
  return new TextEncoder().encode(pdf);
}

const nativeScript = String.raw`
import asyncio, json, os, time
from pathlib import Path
from aiohttp import web
from gideon.core.config import config_dir
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.integrations.knowledge_providers.dir_source import DirSourceProvider
from gideon.integrations.knowledge_providers.feed_source import FeedSourceProvider
from gideon.integrations.knowledge_providers.registry import register_provider
from gideon.integrations.knowledge_providers.web_source import WebSourceProvider
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers import auth
from gideon.interfaces.dashboard.handlers.knowledge import setup_knowledge_routes
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.auth import credentials
from gideon.cognition.knowledge import knowledge_files_dir

async def main(origin):
  home = Path(os.environ["GIDEON_HOME"])
  home.mkdir(parents=True, exist_ok=True)
  (home / "config.json").write_text(json.dumps({"auth": {"login_enabled": True}}), encoding="utf-8")
  assert Path(config_dir()).resolve() == home.resolve(), "native configuration must use the isolated Gideon home"
  credentials.set_password("library-owner", "correct-horse-battery-staple")
  token_auth.use_persistent_secret()
  token_auth.revoke_all_sessions()
  state = ConsoleState(sessions=ConversationDirectory(AppConfig.load()), start_time=time.time(), owner_id="library-owner")
  store = state.knowledge_store
  register_provider(DirSourceProvider(store))
  register_provider(FeedSourceProvider(store))
  register_provider(WebSourceProvider(store))
  files = Path(knowledge_files_dir())
  files.mkdir(parents=True, exist_ok=True)
  missing_id = store.create_typed_item(item_type="document", title="Missing original PDF", content="The saved extracted passage remains readable.", provider="native", extra={
    "mime_type": "application/pdf", "file_path": str(files / "missing-original.pdf"),
    "file_metadata": {"original_filename": "missing-original.pdf"},
  })
  assert missing_id
  source_write_started = asyncio.Event()
  app = web.Application(middlewares=[token_auth.token_auth_middleware(port=10000)])
  app["port"] = 10000
  app["allowed_origins"] = {origin}
  app["state"] = state
  app.router.add_get("/api/auth/status", auth.api_login_status)
  app.router.add_get("/api/auth/session", auth.api_auth_session)
  app.router.add_post("/api/auth/login", auth.api_auth_login)
  app.router.add_post("/api/auth/logout", auth.api_auth_logout)
  setup_knowledge_routes(app)
  control = web.Application()
  async def set_annotation_storage(request):
    body = await request.json()
    store.db.execute("PRAGMA query_only = " + ("ON" if body.get("read_only") else "OFF"))
    return web.json_response({"read_only": bool(body.get("read_only"))})
  async def replace_owner(_request):
    credentials.set_password("reader-second-owner", "correct-horse-battery-staple")
    return web.json_response({"changed": True})
  control.router.add_post("/annotation-storage", set_annotation_storage)
  control.router.add_post("/owner", replace_owner)
  runner = web.AppRunner(app)
  await runner.setup()
  site = web.TCPSite(runner, "127.0.0.1", 0)
  await site.start()
  control_runner = web.AppRunner(control)
  await control_runner.setup()
  control_site = web.TCPSite(control_runner, "127.0.0.1", 0)
  await control_site.start()
  print(json.dumps({"api_port": site._server.sockets[0].getsockname()[1],
    "control_port": control_site._server.sockets[0].getsockname()[1], "missing_id": missing_id}), flush=True)
  try: await asyncio.Event().wait()
  finally:
    await control_runner.cleanup()
    await runner.cleanup()
    store.db.close()

asyncio.run(main(__import__("sys").argv[1]))
`;

afterEach(async () => {
  await browser?.close();
  await vite?.close();
  await native?.stop();
  if (temporary) await rm(temporary, { recursive: true, force: true });
  browser = undefined;
  vite = undefined;
  native = undefined;
  temporary = undefined;
});

describe("Library source-aware reader and durable notes", () => {
  it("opens an authorized PDF, retries a failed passage note, reloads it, and masks old owner and route data", async () => {
    temporary = await mkdtemp(join(tmpdir(), "gideon-library-04-"));
    const script = join(temporary, "native-library.py");
    const entry = join(temporary, "entry.tsx");
    await writeFile(script, nativeScript, "utf8");
    await writeFile(entry, String.raw`
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import { libraryModuleDefinitions } from '${join(repositoryRoot, "apps/assistant/src/features/library/moduleDefinitions.web.ts").replaceAll("\\", "/")}';
      import { ownerScope, readOwnerSession, readLoginStatus, signInOwner, signOutOwner, OwnerSignIn } from '${join(repositoryRoot, "apps/assistant/src/shared/auth.web.tsx").replaceAll("\\", "/")}';
      import { createShellRoute, parseShellRoute, serializeShellRoute } from '${join(repositoryRoot, "apps/assistant/src/shared/shell/shellRoutes.ts").replaceAll("\\", "/")}';
      import { libraryItemRoute } from '${join(repositoryRoot, "apps/assistant/src/features/library/libraryRoutes.ts").replaceAll("\\", "/")}';
      function Root(){
        const [identity,setIdentity]=React.useState(null);const [status,setStatus]=React.useState(null);const [error,setError]=React.useState('');const [route,setRoute]=React.useState(()=>parseShellRoute(location.href,location.origin));
        const module=libraryModuleDefinitions.find(candidate=>candidate.matches(route));const [Module,setModule]=React.useState(null);const [availability,setAvailability]=React.useState('checking');
        React.useEffect(()=>{readLoginStatus().then(setStatus);readOwnerSession().then(setIdentity).catch(()=>{});const pop=()=>setRoute(parseShellRoute(location.href,location.origin));addEventListener('popstate',pop);return()=>removeEventListener('popstate',pop)},[]);
        const navigate=next=>{history.pushState(null,'',serializeShellRoute(next));setRoute(parseShellRoute(location.href,location.origin))};
        React.useEffect(()=>{let live=true;setModule(null);setAvailability('checking');if(!identity)return()=>{live=false};if(!module){setAvailability('missing');return()=>{live=false}}module.resolve(ownerScope(location.origin,identity),route).then(async result=>{if(!live)return;setAvailability(result);if(result==='available'){const loaded=(await module.load()).default;setModule(()=>loaded)}}).catch(()=>{if(live)setAvailability('unavailable')});return()=>{live=false}},[route,identity,module]);
        React.useEffect(()=>{Object.assign(window,{__libraryNavigate:(id)=>navigate(libraryItemRoute({kind:'knowledge',id},route)),__librarySignOut:async()=>{await signOutOwner();setIdentity(null)}})},[route]);
        if(!identity)return React.createElement(OwnerSignIn,{status,error,onRetry:()=>readOwnerSession().then(setIdentity).catch(()=>{}),onSignIn:async(u,p,t)=>{try{await signInOwner(u,p,t);setIdentity(await readOwnerSession())}catch(e){setError(String(e))}}});
        if(!Module)return React.createElement('p',{role:'status','data-module-state':availability},'Opening Library…');
        return React.createElement(Module,{route,scope:ownerScope(location.origin,identity),navigate});
      }
      createRoot(document.getElementById('root')).render(React.createElement(Root));
    `, "utf8");

    const webPort = await availablePort();
    const origin = `http://127.0.0.1:${webPort}`;
    native = await startNativeServer({ script, origin, repositoryRoot });
    vite = await startViteEntryServer({ root: join(repositoryRoot, "apps/assistant"), port: webPort, entryFile: entry, apiOrigin: native.apiOrigin });
    browser = await startBrowserHarness({ windowSize: { width: 1440, height: 1000 } });
    await browser.navigate(`http://127.0.0.1:${webPort}/assistant/apps?v=1&view=workspace&placement=knowledge`);
    await browser.waitFor("document.querySelector('#gideon-password')", "Library sign-in");
    const setValue = `(id,value)=>{const field=document.getElementById(id);if(!field)throw new Error('Missing form field '+id);const prototype=field instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value').set.call(field,value);field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}))}`;
    await browser.evaluate(`(${setValue})('gideon-username','library-owner')`);
    await browser.evaluate(`(${setValue})('gideon-password','correct-horse-battery-staple')`);
    await browser.evaluate("document.querySelector('form')?.requestSubmit()");
    await browser.waitFor("document.querySelector('[aria-label=\"Library sections\"]')", "native Library home");

    await browser.evaluate("[...document.querySelectorAll('button')].find(button=>button.textContent==='Import')?.click()");
    await browser.waitFor("document.querySelector('#library-pdf-upload')", "PDF import controls");
    const pdf = Buffer.from(makePdf()).toString("base64");
    await browser.evaluate(`(()=>{const bytes=Uint8Array.from(atob('${pdf}'),char=>char.charCodeAt(0));const file=new File([bytes],'reader-source.pdf',{type:'application/pdf'});const input=document.querySelector('#library-pdf-upload');const transfer=new DataTransfer();transfer.items.add(file);input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
    await browser.waitFor("document.querySelector('[data-import-item-id] button')", "imported PDF record action");
    const importedId = await browser.evaluate<string>("document.querySelector('[data-import-item-id]')?.getAttribute('data-import-item-id')||''");
    expect(importedId).toBeTruthy();
    await browser.evaluate("[...document.querySelectorAll('[data-import-item-id] button')].find(button=>button.textContent==='Open item')?.click()");
    await browser.waitFor("document.querySelector('[data-library-reader-key] h2')?.textContent==='reader-source.pdf'", "PDF reader route");
    await browser.waitFor("document.querySelector('[data-pdf-state=ready] iframe[title^=\"PDF document:\"]')", "authorized original PDF");
    const reader = await browser.evaluate<{ width: number; pdfState?: string; provider?: string; source?: string }>("(()=>{const panel=document.querySelector('[data-pdf-state=ready]');const reader=document.querySelector('.gideon-library-reader');const meta=reader?.querySelector('.gideon-library-reader__metadata')?.textContent;return {width:reader?.getBoundingClientRect().width||0,pdfState:panel?.getAttribute('data-pdf-state'),provider:meta?.includes('native')?'native':'',source:meta||''}})()");
    expect(reader.width).toBeGreaterThan(900);
    expect(reader.pdfState).toBe("ready");
    expect(reader.provider).toBe("native");
    expect(reader.source).toContain("reader-source.pdf");

    await browser.evaluate(`(${setValue})('library-annotation-quote','A native reader passage.')`);
    await browser.evaluate(`(${setValue})('library-annotation-note','Keep this passage for review.')`);
    await browser.evaluate("document.querySelector('form[aria-label=\"Add passage note\"] button[type=submit]')?.click()");
    await browser.waitFor("document.querySelector('[data-annotation-id]')?.textContent.includes('Keep this passage for review.')", "saved passage note");

    await capture("library-04-desktop.png");
    await browser.command("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await browser.waitFor("document.querySelector('.gideon-library-reader__pdf-frame')?.getBoundingClientRect().width<=390", "narrow PDF reader layout");
    await capture("library-04-narrow.png");
    const narrow = await browser.evaluate<{ width: number; scrollWidth: number }>("(()=>({width:document.querySelector('.gideon-library-reader')?.getBoundingClientRect().width||0,scrollWidth:document.documentElement.scrollWidth}))()");
    expect(narrow.width).toBeLessThanOrEqual(390);
    expect(narrow.scrollWidth).toBeLessThanOrEqual(390);

    await browser.command("Emulation.clearDeviceMetricsOverride");
    const beforeReload = await browser.evaluate<number>("performance.timeOrigin");
    const readerUrl = await browser.evaluate<string>("location.href");
    await browser.navigate(readerUrl);
    await browser.waitFor(`performance.timeOrigin>${beforeReload} && document.querySelector('[data-annotation-id]')?.textContent.includes('Keep this passage for review.')`, "persisted note after a new document load");

    const readOnly = await fetch(`${native.controlOrigin}/annotation-storage`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ read_only: true }) });
    expect(readOnly.ok).toBe(true);
    await browser.evaluate(`(${setValue})('library-annotation-quote','A retryable native passage.')`);
    await browser.evaluate(`(${setValue})('library-annotation-note','This draft survives a storage failure.')`);
    await browser.evaluate("document.querySelector('form[aria-label=\"Add passage note\"] button[type=submit]')?.click()");
    await browser.waitFor("document.querySelector('[data-annotation-error]')?.textContent", "failed native annotation write");
    const retained = await browser.evaluate<{ quote?: string; note?: string }>("(()=>({quote:document.querySelector('#library-annotation-quote')?.value,note:document.querySelector('#library-annotation-note')?.value}))()");
    expect(retained).toEqual({ quote: "A retryable native passage.", note: "This draft survives a storage failure." });
    const writable = await fetch(`${native.controlOrigin}/annotation-storage`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ read_only: false }) });
    expect(writable.ok).toBe(true);
    await browser.evaluate("document.querySelector('form[aria-label=\"Add passage note\"] button[type=submit]')?.click()");
    await browser.waitFor("[...document.querySelectorAll('[data-annotation-id]')].some(row=>row.textContent.includes('This draft survives a storage failure.'))", "retry saved the retained note");

    await browser.evaluate("(()=>{window.__libraryFirstCommit=[];const target=document.querySelector('#root');window.__libraryMaskObserver=new MutationObserver(()=>window.__libraryFirstCommit.push({oldNote:document.body.innerText.includes('Keep this passage for review.'),oldReader:document.body.innerText.includes('reader-source.pdf')}));window.__libraryMaskObserver.observe(target,{childList:true,subtree:true,characterData:true});return true})()");
    const missingId = String(native.startup.missing_id ?? "");
    await browser.evaluate(`window.__libraryNavigate('${missingId}')`);
    await browser.waitFor("document.querySelector('[data-pdf-state=missing]')", "truthful missing PDF fallback");
    expect(await browser.evaluate<boolean>("document.body.innerText.includes('The original PDF is missing. Showing the extracted text saved with this item.') && document.body.innerText.includes('The saved extracted passage remains readable.')")).toBe(true);
    const routeMask = await browser.evaluate<Array<{ oldNote?: boolean; oldReader?: boolean }>>("window.__libraryFirstCommit");
    expect(routeMask.length).toBeGreaterThan(0);
    expect(routeMask[0].oldNote).toBe(false);
    expect(routeMask[0].oldReader).toBe(false);
    await browser.evaluate("window.__libraryMaskObserver?.disconnect()");

    await browser.evaluate(`window.__libraryNavigate('${importedId}')`);
    await browser.waitFor("document.querySelector('[data-annotation-id]')?.textContent.includes('Keep this passage for review.')", "return to the annotated PDF before owner change");

    const changedOwner = await fetch(`${native.controlOrigin}/owner`, { method: "POST" });
    expect(changedOwner.ok).toBe(true);
    await browser.evaluate("window.__librarySignOut()");
    await browser.waitFor("document.querySelector('#gideon-password')", "signed-out owner boundary");
    await browser.evaluate("(()=>{window.__libraryOwnerFirstCommit=[];const target=document.querySelector('#root');window.__libraryOwnerObserver=new MutationObserver(()=>window.__libraryOwnerFirstCommit.push({oldNote:document.body.innerText.includes('Keep this passage for review.'),oldReader:document.body.innerText.includes('reader-source.pdf')}));window.__libraryOwnerObserver.observe(target,{childList:true,subtree:true,characterData:true});return true})()");
    await browser.evaluate(`(${setValue})('gideon-username','reader-second-owner')`);
    await browser.evaluate(`(${setValue})('gideon-password','correct-horse-battery-staple')`);
    await browser.evaluate("document.querySelector('form')?.requestSubmit()");
    await browser.waitFor("document.querySelector('[data-library-reader-key]')", "second owner reader route");
    const ownerMask = await browser.evaluate<Array<{ oldNote?: boolean; oldReader?: boolean }>>("window.__libraryOwnerFirstCommit");
    expect(ownerMask.length).toBeGreaterThan(0);
    expect(ownerMask[0].oldNote).toBe(false);
    expect(ownerMask[0].oldReader).toBe(false);
    await browser.evaluate("window.__libraryOwnerObserver?.disconnect()");
  }, 60000);
});
