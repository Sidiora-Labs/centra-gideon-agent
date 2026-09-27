import { mkdtemp, rm, writeFile } from "node:fs/promises";
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

const nativeScript = String.raw`
import asyncio, json, os
from pathlib import Path
from types import SimpleNamespace
from aiohttp import web
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.integrations.knowledge_providers.dir_source import DirSourceProvider
from gideon.integrations.knowledge_providers.feed_source import FeedSourceProvider
from gideon.integrations.knowledge_providers.registry import register_provider
from gideon.integrations.knowledge_providers.web_source import WebSourceProvider
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers import auth
from gideon.interfaces.dashboard.handlers.knowledge import (
  create_watched_source, get_item, ingest_file, list_items, list_source_recipes,
  list_watched_sources, update_watched_source,
)
from gideon.security.auth import credentials

async def main(origin):
  home = Path(os.environ["GIDEON_HOME"])
  home.mkdir(parents=True, exist_ok=True)
  (home / "config.json").write_text(json.dumps({"auth": {"login_enabled": True}}), encoding="utf-8")
  credentials.set_password("library-owner", "correct-horse-battery-staple")
  token_auth.use_persistent_secret()
  token_auth.revoke_all_sessions()
  store = KnowledgeStore(str(home / "knowledge.db"))
  register_provider(DirSourceProvider(store))
  register_provider(FeedSourceProvider(store))
  register_provider(WebSourceProvider(store))
  paused_id = store.create_source(name="Paused source", provider="watched-feed", kind="feed",
    spec={"kind":"rss", "url":"https://example.invalid/feed.xml"}, enabled=False)
  failed_id = store.create_source(name="Failed source", provider="watched-feed", kind="feed",
    spec={"kind":"rss", "url":"https://example.invalid/failed.xml"})
  store.record_poll(failed_id, cursor="", new_count=0, health_status="error", error_summary="The last native feed poll failed.")
  orphan_id = store.create_source(name="Unenrolled source", provider="removed-provider", kind="external", spec={})
  app = web.Application(middlewares=[token_auth.token_auth_middleware(port=10000)])
  app["port"] = 10000
  app["allowed_origins"] = {origin}
  app["state"] = SimpleNamespace(knowledge_store=store)
  app.router.add_get("/api/auth/status", auth.api_login_status)
  app.router.add_get("/api/auth/session", auth.api_auth_session)
  app.router.add_post("/api/auth/login", auth.api_auth_login)
  app.router.add_post("/api/auth/logout", auth.api_auth_logout)
  app.router.add_get("/api/knowledge/items", list_items)
  app.router.add_post("/api/knowledge/ingest", ingest_file)
  app.router.add_get("/api/knowledge/items/{id}", get_item)
  app.router.add_get("/api/knowledge/sources", list_watched_sources)
  app.router.add_post("/api/knowledge/sources", create_watched_source)
  app.router.add_patch("/api/knowledge/sources/{id}", update_watched_source)
  app.router.add_get("/api/knowledge/source-recipes", list_source_recipes)
  runner = web.AppRunner(app)
  await runner.setup()
  site = web.TCPSite(runner, "127.0.0.1", 0)
  await site.start()
  port = site._server.sockets[0].getsockname()[1]
  print(json.dumps({"api_port": port, "control_port": port, "paused_id": paused_id,
    "failed_id": failed_id, "orphan_id": orphan_id}), flush=True)
  try: await asyncio.Event().wait()
  finally:
    await runner.cleanup()
    store.db.close()

asyncio.run(main(__import__("sys").argv[1]))
`;

function makePdf(): Uint8Array {
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    "<< /Length 63 >>\nstream\nBT /F1 12 Tf 24 100 Td (Native PDF source record) Tj ET\nendstream",
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

const browserSetValue = `(id,value)=>{const field=document.getElementById(id);if(!field)throw new Error('Missing form field '+id);const prototype=field instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value').set.call(field,value);field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));return 'ok'}`;

describe("Library import and watched source journey", () => {
  it("stores a real PDF as a native item and shows registered provider and persisted source health", async () => {
    temporary = await mkdtemp(join(tmpdir(), "gideon-library-03-"));
    const script = join(temporary, "native-library.py");
    const entry = join(temporary, "entry.tsx");
    await writeFile(script, nativeScript, "utf8");
    await writeFile(entry, String.raw`
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import { libraryModuleDefinitions } from '${join(repositoryRoot, "apps/assistant/src/features/library/moduleDefinitions.web.ts").replaceAll("\\", "/")}';
      import { ownerScope, readOwnerSession, readLoginStatus, signInOwner, OwnerSignIn } from '${join(repositoryRoot, "apps/assistant/src/shared/auth.web.tsx").replaceAll("\\", "/")}';
      import { createShellRoute, parseShellRoute, serializeShellRoute } from '${join(repositoryRoot, "apps/assistant/src/shared/shell/shellRoutes.ts").replaceAll("\\", "/")}';
      function Root(){
        const [identity,setIdentity]=React.useState(null);const [status,setStatus]=React.useState(null);const [error,setError]=React.useState('');const [route,setRoute]=React.useState(()=>parseShellRoute(location.href,location.origin));
        const module=libraryModuleDefinitions.find(candidate=>candidate.matches(route));const [Module,setModule]=React.useState(null);const [availability,setAvailability]=React.useState('checking');
        React.useEffect(()=>{readLoginStatus().then(setStatus);readOwnerSession().then(setIdentity).catch(()=>{});const pop=()=>setRoute(parseShellRoute(location.href,location.origin));addEventListener('popstate',pop);return()=>removeEventListener('popstate',pop)},[]);
        const navigate=next=>{history.pushState(null,'',serializeShellRoute(next));setRoute(parseShellRoute(location.href,location.origin))};
        React.useEffect(()=>{let live=true;setModule(null);setAvailability('checking');if(!identity)return()=>{live=false};if(!module){setAvailability('missing');return()=>{live=false}}module.resolve(ownerScope(location.origin,identity),route).then(async result=>{if(!live)return;setAvailability(result);if(result==='available'){const loaded=(await module.load()).default;setModule(()=>loaded)}}).catch(()=>{if(live)setAvailability('unavailable')});return()=>{live=false}},[route,identity,module]);
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
    browser = await startBrowserHarness({ windowSize: { width: 1280, height: 900 } });
    await browser.navigate(`http://127.0.0.1:${webPort}/assistant/apps?v=1&view=workspace&placement=knowledge`);
    await browser.waitFor("document.querySelector('#gideon-password')", "Library sign-in");
    await browser.evaluate(`(${browserSetValue})('gideon-username','library-owner')`);
    await browser.evaluate(`(${browserSetValue})('gideon-password','correct-horse-battery-staple')`);
    await browser.evaluate("document.querySelector('form')?.requestSubmit()");
    await browser.waitFor("document.querySelector('[aria-label=\"Library sections\"]')", "native Library home");

    await browser.evaluate("[...document.querySelectorAll('button')].find(button=>button.textContent==='Import')?.click()");
    await browser.waitFor("document.querySelector('#library-pdf-upload')", "PDF import panel");
    const pdf = Buffer.from(makePdf()).toString("base64");
    await browser.evaluate(`(()=>{const bytes=Uint8Array.from(atob('${pdf}'),char=>char.charCodeAt(0));const file=new File([bytes],'native-guide.pdf',{type:'application/pdf'});const input=document.querySelector('#library-pdf-upload');const transfer=new DataTransfer();transfer.items.add(file);input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
    await browser.waitFor("document.querySelector('[data-import-item-id]')", "native PDF record");
    const imported = await browser.evaluate<{ id?: string; status?: string; text?: string | null; href?: string | null }>("(()=>{const panel=document.querySelector('[data-import-item-id]');const link=panel?.querySelector('a');return {id:panel?.getAttribute('data-import-item-id'),status:panel?.querySelector('[data-extraction-status]')?.getAttribute('data-extraction-status'),text:panel?.textContent,href:link?.getAttribute('href')}})()");
    expect(imported.id).toBeTruthy();
    expect(imported.text).toContain("native-guide.pdf");
    expect(imported.text).toContain("Provider:");
    expect(imported.status).toBe("queued");
    expect(imported.href).toContain(encodeURIComponent(imported.id!));

    await browser.evaluate("[...document.querySelectorAll('button')].find(button=>button.textContent==='Sources')?.click()");
    await browser.waitFor("document.querySelector('#library-source-location')", "native source setup");
    await browser.evaluate(`(${browserSetValue})('library-source-location','https://github.com/astral-sh/uv')`);
    await browser.waitFor("document.querySelector('input[name=library-source-recipe]')", "native matching source recipe");
    await browser.evaluate("document.querySelector('input[name=library-source-recipe]')?.click()");
    await browser.evaluate(`(${browserSetValue})('library-source-name','Gideon releases')`);
    await browser.waitFor("document.querySelector('#library-source-name')?.value==='Gideon releases' && !document.querySelector('form[aria-label=\"Add watched source\"] button[type=submit]')?.disabled", "ready watched source form");
    await browser.evaluate("document.querySelector('form[aria-label=\"Add watched source\"] button[type=submit]')?.click()");
    await browser.waitFor("[...document.querySelectorAll('[data-source-id]')].some(row=>row.querySelector('h5')?.textContent==='Gideon releases')", "saved native watched source");
    const sourceStatus = await browser.evaluate<Array<{ name?: string | null; text?: string | null }>>("[...document.querySelectorAll('[data-source-id]')].map(row=>({name:row.querySelector('h5')?.textContent,text:row.textContent}))");
    expect(sourceStatus.some(row => row.name === "Gideon releases" && row.text?.includes("Not checked yet"))).toBe(true);
    expect(sourceStatus.some(row => row.name === "Paused source" && row.text?.includes("Paused"))).toBe(true);
    expect(sourceStatus.some(row => row.name === "Failed source" && row.text?.includes("Failed") && row.text.includes("last native feed poll failed"))).toBe(true);
    expect(sourceStatus.some(row => row.name === "Unenrolled source" && row.text?.includes("Provider unavailable"))).toBe(true);

    const newSource = await browser.evaluate<string | undefined>("[...document.querySelectorAll('[data-source-id]')].find(source=>source.querySelector('h5')?.textContent==='Gideon releases')?.getAttribute('data-source-id')||undefined");
    expect(newSource).toMatch(/^src-[a-f0-9]{8}$/);
    await browser.evaluate("[...document.querySelectorAll('[data-source-id]')].find(row=>row.querySelector('h5')?.textContent==='Gideon releases')?.querySelector('button')?.click()");
    await browser.waitFor("[...document.querySelectorAll('[data-source-id]')].find(row=>row.querySelector('h5')?.textContent==='Gideon releases')?.textContent.includes('Paused')", "persisted native pause");
  }, 60000);
});
