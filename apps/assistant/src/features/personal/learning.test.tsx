import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterAll, describe, expect, it } from 'vitest'
import { startBrowserHarness, startViteEntryServer, type BrowserHarness } from '../../../test-support/browserHarness'

const root = resolve(process.cwd(), '../..')
const processes: ChildProcessWithoutNullStreams[] = []
const directories: string[] = []
let vite: Awaited<ReturnType<typeof startViteEntryServer>> | undefined
let browser: BrowserHarness | undefined

afterAll(async () => {
  await browser?.close()
  for (const child of processes) child.kill('SIGTERM')
  if (vite) await vite.close()
  for (const directory of directories) await rm(directory, { recursive: true, force: true })
})

async function port(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(ready => server.listen(0, '127.0.0.1', ready))
  const address = server.address()
  const selected = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(closed => server.close(() => closed()))
  return selected
}

const serverScript = String.raw`
import asyncio, json, os, sys
from pathlib import Path
from types import SimpleNamespace
from aiohttp import web
import gideon.core.config.loader as loader
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.cognition.learning import proposals
from gideon.interfaces.dashboard.handlers import capabilities_knowledge_capture, capabilities_knowledge_reviews, learning

async def main(port):
  home=Path(os.environ['GIDEON_HOME']);loader.config_dir=lambda:home
  (home/'config.json').write_text(json.dumps({'learning':{'enabled':True}}),encoding='utf-8')
  store=KnowledgeStore(str(home/'knowledge.sqlite3'))
  state=SimpleNamespace(knowledge_store=store,context_builder=None)
  app=web.Application();app['state']=state
  capabilities_knowledge_capture.register(app);capabilities_knowledge_reviews.register(app);learning.register_learning_routes(app)
  _, proposal=proposals.enqueue(kind='skill',title='Review gate record',body='A native proposal for the reviewer gate test.',provenance='capture',source_excerpt='Native evidence excerpt',evidence_refs=['capture:learning-test'],evidence_strength='direct',occurrences=1,min_evidence=1)
  if proposal is None: raise RuntimeError('Native proposal fixture was not filed')
  runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',int(port));await site.start()
  print(json.dumps({'proposal_id':proposal.id}),flush=True)
  try: await asyncio.Event().wait()
  finally: await runner.cleanup();store.db.close()

asyncio.run(main(int(sys.argv[1])))
`

async function startServer(): Promise<{ origin: string; proposalId: string }> {
  const home = await mkdtemp(join(tmpdir(), 'gideon-learning-native-home-')); directories.push(home)
  const selected = await port()
  const child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-c', serverScript, String(selected)], {
    cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: join(root, 'runtime') },
  })
  processes.push(child)
  const line = await new Promise<string>((ready, fail) => {
    let output = ''; let errors = ''
    const timeout = setTimeout(() => fail(new Error(`Native Learning server timed out: ${errors}`)), 20000)
    child.stdout.on('data', chunk => { output += String(chunk); if (output.includes('\n')) { clearTimeout(timeout); ready(output.split('\n')[0]) } })
    child.stderr.on('data', chunk => { errors += String(chunk) })
    child.once('exit', code => { clearTimeout(timeout); fail(new Error(`Native Learning server exited ${code}: ${errors}`)) })
  })
  return { origin: `http://127.0.0.1:${selected}`, proposalId: (JSON.parse(line) as { proposal_id: string }).proposal_id }
}

describe('Learning native records and review gate', () => {
  it('retains capture history and denies proposal decisions without a human dashboard actor', async () => {
    const native = await startServer()
    const create = await fetch(`${native.origin}/api/capabilities/knowledge/captures`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ request_id: 'learning-receipt-001', text: 'A real native capture receipt.' }),
    })
    expect(create.status).toBe(200)
    const receipt = await create.json() as Record<string, unknown>
    expect(receipt.status).toBe('needs_review')
    expect(receipt.text).toBe('A real native capture receipt.')
    expect(receipt.id).toEqual(expect.any(String))
    expect(receipt.revision).toBe(1)

    const historyResponse = await fetch(`${native.origin}/api/capabilities/knowledge/captures?limit=20&offset=0`)
    expect(historyResponse.status).toBe(200)
    const history = await historyResponse.json() as { items: Array<Record<string, unknown>>; total: number }
    expect(history.total).toBe(1)
    expect(history.items[0]).toMatchObject({ id: receipt.id, request_id: 'learning-receipt-001', text: 'A real native capture receipt.', status: 'needs_review', revision: 1 })

    const decision = await fetch(`${native.origin}/api/learning/proposals/${encodeURIComponent(native.proposalId)}/accept`, { method: 'POST' })
    expect(decision.status).toBe(403)
    expect(await decision.json()).toMatchObject({ error: expect.stringMatching(/human/i) })
    const inbox = await fetch(`${native.origin}/api/learning/proposals`)
    expect(inbox.status).toBe(200)
    const pending = await inbox.json() as { rows: Array<{ id: string; status: string; evidence_refs: string[] }> }
    expect(pending.rows).toContainEqual(expect.objectContaining({ id: native.proposalId, status: 'pending', evidence_refs: ['capture:learning-test'] }))
  }, 30000)

  it('keeps delayed proposal details bound to the selected record and clears Learning after the owner session expires', async () => {
    const frontPort = await port()
    const origin = `http://127.0.0.1:${frontPort}`
    const native = await startBrowserApi(origin)
    const entryDirectory = await mkdtemp(join(root, 'apps/assistant/.learning-review-entry-'))
    directories.push(entryDirectory)
    const entryFile = join(entryDirectory, 'learning-entry.tsx')
    await writeFile(entryFile, `import React from 'react';
import { createRoot } from 'react-dom/client';
import { ownerScope, readOwnerSession, readLoginStatus, signInOwner, OwnerSignIn, signOutOwner } from '/src/shared/auth.web.tsx';
import { parseShellRoute, createShellRoute } from '/src/shared/shell/shellRoutes.ts';
import LearningWorkspace from '/src/features/personal/LearningWorkspace.web.tsx';
window.signOutOwner = signOutOwner;
function Root() {
  const [identity, setIdentity] = React.useState(null);
  const [status, setStatus] = React.useState(null);
  const [error, setError] = React.useState('');
  React.useEffect(() => {
    void readLoginStatus().then(setStatus);
    void readOwnerSession().then(setIdentity).catch(() => undefined);
  }, []);
  if (!identity) return <OwnerSignIn status={status} error={error} onRetry={() => { void readOwnerSession().then(setIdentity).catch(() => undefined); }} onSignIn={async (username, password, token) => {
    try { await signInOwner(username, password, token); setIdentity(await readOwnerSession()); }
    catch (cause) { setError(String(cause)); }
  }} />;
  const route = parseShellRoute(location.href, location.origin);
  if (route.kind !== 'route') return <p role="alert">Learning route could not be opened.</p>;
  return <LearningWorkspace route={route} scope={ownerScope(location.origin, identity)} onReturn={() => location.assign(createShellRoute('ideas').href)} />;
}
createRoot(document.getElementById('root')!).render(<Root />);
`, 'utf8')
    vite = await startViteEntryServer({ root: join(root, 'apps/assistant'), port: frontPort, entryFile, apiOrigin: native.origin })
    browser = await startBrowserHarness()
    await browser.navigate(`${origin}/assistant/ideas?v=1&view=workspace&placement=learning`)
    await browser.waitFor("document.querySelector('#gideon-password')", 'real owner sign-in form')
    await browser.evaluate(`(()=>{const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};set('gideon-username','learning-owner');set('gideon-password','native-learning-password');document.querySelector('form').requestSubmit();return true})()`)
    await browser.waitFor("document.body.innerText.includes('Slow proposal A') && document.body.innerText.includes('Fast proposal B')", 'native proposal inbox')
    await browser.evaluate("Array.from(document.querySelectorAll('.gideon-learning__proposal')).find(button=>button.innerText.includes('Slow proposal A'))?.click()")
    await browser.waitFor("document.body.innerText.includes('Slow proposal A') && document.body.innerText.includes('Loading proposal details…')", 'slow proposal selection')
    expect(await browser.evaluate<boolean>("document.querySelector('.gideon-learning__decision button') === null")).toBe(true)
    await browser.evaluate("Array.from(document.querySelectorAll('.gideon-learning__proposal')).find(button=>button.innerText.includes('Fast proposal B'))?.click()")
    await browser.waitFor("document.body.innerText.includes('Details belonging to fast proposal B.')", 'selected proposal B details')
    await new Promise(resolve => setTimeout(resolve, 1000))
    const selectedDetails = await browser.evaluate<string>("document.querySelector('.gideon-learning__review')?.innerText || ''")
    expect(selectedDetails).toContain('Fast proposal B')
    expect(selectedDetails).toContain('Details belonging to fast proposal B.')
    expect(selectedDetails).not.toContain('Details belonging to slow proposal A.')
    await browser.evaluate("document.querySelector('.gideon-learning__ack input')?.click()")
    await browser.evaluate("Array.from(document.querySelectorAll('.gideon-learning__decision button')).find(button=>button.innerText.includes('Dismiss'))?.click()")
    await browser.waitFor("document.querySelector('.gideon-learning__review')?.innerText.includes('Saving your review…')", 'delayed proposal decision in progress')
    expect(await browser.evaluate<boolean>("Array.from(document.querySelectorAll('.gideon-learning__review header button')).find(button=>button.innerText==='Close')?.disabled === true")).toBe(true)
    await browser.waitFor("document.querySelector('[aria-labelledby=\"learning-proposals\"]')?.innerText.includes('Slow proposal A') && !document.querySelector('[aria-labelledby=\"learning-proposals\"]')?.innerText.includes('Fast proposal B')", 'only selected proposal was dismissed')
    expect(await browser.evaluate<boolean>("Array.from(document.querySelectorAll('.gideon-workspace-actions button')).find(button=>button.innerText==='Refresh')?.disabled === false")).toBe(true)

    await browser.evaluate("window.signOutOwner()")
    await browser.evaluate("Array.from(document.querySelectorAll('.gideon-workspace-actions button')).find(button=>button.innerText==='Refresh')?.click()")
    await browser.waitFor("document.body.innerText.includes('Your session expired. Sign in again to view Learning.')", 'session expiry error')
    const expiredView = await browser.evaluate<string>("document.querySelector('.gideon-workspace-scroll')?.innerText || ''")
    expect(expiredView).not.toContain('Original text retained in Learning history.')
    expect(expiredView).not.toContain('Slow proposal A')
    expect(browser.diagnostics().filter(message => !message.includes('Download the React DevTools for a better development experience:') && !message.includes('net::ERR_ABORTED'))).toEqual([])
  }, 120000)
})

async function startBrowserApi(origin: string): Promise<{ origin: string }> {
  const home = await mkdtemp(join(tmpdir(), 'gideon-learning-browser-home-'))
  directories.push(home)
  const selected = await port()
  const child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-c', browserServer, origin, String(selected)], {
    cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: join(root, 'runtime') },
  })
  processes.push(child)
  const line = await new Promise<string>((ready, fail) => {
    let output = ''; let errors = ''
    const timeout = setTimeout(() => fail(new Error(`Native Learning browser server timed out: ${errors}`)), 20000)
    child.stdout.on('data', chunk => { output += String(chunk); if (output.includes('\n')) { clearTimeout(timeout); ready(output.split('\n')[0]) } })
    child.stderr.on('data', chunk => { errors += String(chunk) })
    child.once('exit', code => { clearTimeout(timeout); fail(new Error(`Native Learning browser server exited ${code}: ${errors}`)) })
  })
  const ready = JSON.parse(line) as { api_port: number }
  return { origin: `http://127.0.0.1:${ready.api_port}` }
}

const browserServer = String.raw`
import asyncio, json, os, sys, time
from pathlib import Path
from types import SimpleNamespace
from aiohttp import web
import gideon.core.config.loader as loader
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.cognition.learning import proposals
from gideon.interfaces.dashboard import session_store, token_auth
from gideon.interfaces.dashboard.handlers import auth, capabilities_knowledge_capture, capabilities_knowledge_reviews, learning
from gideon.security.auth import credentials

async def main(origin, selected_port):
  home=Path(os.environ['GIDEON_HOME']);loader.config_dir=lambda:home;credentials.config_dir=lambda:home;session_store.config_dir=lambda:home
  (home/'config.json').write_text(json.dumps({'auth':{'login_enabled':True},'learning':{'enabled':True},'evals':{'enabled':False}}),encoding='utf-8')
  credentials.set_password('learning-owner','native-learning-password');token_auth.use_persistent_secret();token_auth.revoke_all_sessions()
  store=KnowledgeStore(str(home/'knowledge.sqlite3'))
  state=SimpleNamespace(knowledge_store=store,context_builder=None,_background_tasks=set())
  proposal_a=proposals.enqueue(kind='skill',title='Slow proposal A',body='Details belonging to slow proposal A.',provenance='capture',source_excerpt='Evidence for slow proposal A.',evidence_refs=['capture:learning-a'],evidence_strength='direct',occurrences=1,min_evidence=1)[1]
  proposal_b=proposals.enqueue(kind='skill',title='Fast proposal B',body='Details belonging to fast proposal B.',provenance='capture',source_excerpt='Evidence for fast proposal B.',evidence_refs=['capture:learning-b'],evidence_strength='direct',occurrences=1,min_evidence=1)[1]
  @web.middleware
  async def proposal_detail_delay(request, handler):
    if request.method=='GET' and request.path==f'/api/learning/proposals/{proposal_a.id}': await asyncio.sleep(1.2)
    if request.method=='GET' and request.path==f'/api/learning/proposals/{proposal_b.id}': await asyncio.sleep(0.35)
    if request.method=='DELETE' and request.path==f'/api/learning/proposals/{proposal_b.id}': await asyncio.sleep(0.7)
    return await handler(request)
  app=web.Application(middlewares=[proposal_detail_delay,token_auth.token_auth_middleware(port=10000)])
  app['port']=10000;app['allowed_origins']={origin};app['state']=state
  app.router.add_get('/api/auth/status',auth.api_login_status);app.router.add_get('/api/auth/session',auth.api_auth_session)
  app.router.add_post('/api/auth/login',auth.api_auth_login);app.router.add_post('/api/auth/logout',auth.api_auth_logout)
  capabilities_knowledge_capture.register(app);capabilities_knowledge_reviews.register(app);learning.register_learning_routes(app)
  app['capability_capture_inbox'].create('learning-capture-browser','Original text retained in Learning history.')
  runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',int(selected_port));await site.start()
  print(json.dumps({'api_port':site._server.sockets[0].getsockname()[1]}),flush=True)
  try: await asyncio.Event().wait()
  finally: await runner.cleanup();store.db.close()

asyncio.run(main(sys.argv[1],sys.argv[2]))
`
