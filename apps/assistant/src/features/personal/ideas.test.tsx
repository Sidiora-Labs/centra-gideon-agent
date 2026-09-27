import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterAll, describe, expect, it } from 'vitest'
import { startBrowserHarness, startViteEntryServer, type BrowserHarness } from '../../../test-support/browserHarness'

const root = resolve(process.cwd(), '../..')
const children: ChildProcessWithoutNullStreams[] = []
const directories: string[] = []
let vite: Awaited<ReturnType<typeof startViteEntryServer>> | undefined
let browser: BrowserHarness | undefined

afterAll(async () => {
  await browser?.close()
  for (const child of children) child.kill('SIGTERM')
  if (vite) await vite.close()
  for (const directory of directories) await rm(directory, { recursive: true, force: true })
})

async function availablePort(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(done => server.close(() => done()))
  return port
}

const nativeServer = String.raw`
import asyncio, json, os, sys, time
from pathlib import Path
from types import SimpleNamespace
from aiohttp import web
import gideon.core.config.loader as loader
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.cognition.learning import proposals
from gideon.cognition.suggestions import SuggestionsCache, api_suggestions
from gideon.interfaces.dashboard import session_store, token_auth
from gideon.interfaces.dashboard.handlers import auth
from gideon.interfaces.dashboard.handlers import capabilities_knowledge_ideas, capabilities_knowledge_capture, capabilities_knowledge_reviews, learning
from gideon.security.auth import credentials
from gideon.workspace.capabilities.knowledge.ideas import IdeaLists
from gideon.workspace.capabilities.knowledge.idea_format import preview

async def main(origin):
  home = Path(os.environ['GIDEON_HOME'])
  loader.config_dir = lambda: home
  credentials.config_dir = lambda: home
  session_store.config_dir = lambda: home
  (home / 'config.json').write_text(json.dumps({'auth': {'login_enabled': True}, 'learning': {'enabled': True}, 'evals': {'enabled': False}}), encoding='utf-8')
  credentials.set_password('personal-owner', 'native-owner-password')
  token_auth.use_persistent_secret(); token_auth.revoke_all_sessions()
  store = KnowledgeStore(str(home / 'knowledge.db'))
  now = '2026-09-27T10:00:00+00:00'
  content = f'''---\nid: 50d3e623-0be4-4e04-8ca9-68109c5c1267\ntitle: Native saved ideas\ncategory: personal\nstatus: draft\ncreated: {now}\nmodified: {now}\ntags:\n  - idea-loom\n---\n# Prompt\nExplore a grounded next step from captured notes.\n## Ideas\n1. Build a source-linked learning plan\n'''
  idea_preview = preview(content)
  IdeaLists(store, home).import_list({'request_id': 'idea-list-seed-001', 'content': content, 'preview_id': idea_preview['preview_id'], 'expected_hash': ''})
  state = SimpleNamespace(knowledge_store=store, context_builder=None, _background_tasks=set())
  cache = SuggestionsCache(); cache.suggestions = ['Use this cached prompt in a draft']; cache.generated_at = time.time()
  state._suggestions_cache = cache
  proposals.enqueue(kind='skill', title='Review skill proposal', body='A proposal body backed by a real local test record.', target='learning-test', provenance='local_capture', source_excerpt='The source capture supports this proposal.', evidence_refs=['capture:learning-evidence-1'], evidence_strength='direct', confidence=0.91, occurrences=1, min_evidence=1)
  @web.middleware
  async def browser_transition_delay(request, handler):
    if request.method == 'GET' and request.path in ('/api/capabilities/knowledge/ideas', '/api/suggestions'):
      await asyncio.sleep(0.4)
    return await handler(request)
  app = web.Application(middlewares=[browser_transition_delay, token_auth.token_auth_middleware(port=10000)])
  app['port'] = 10000; app['allowed_origins'] = {origin}; app['state'] = state
  app.router.add_get('/api/auth/status', auth.api_login_status); app.router.add_get('/api/auth/session', auth.api_auth_session)
  app.router.add_post('/api/auth/login', auth.api_auth_login); app.router.add_post('/api/auth/logout', auth.api_auth_logout)
  app.router.add_get('/api/suggestions', api_suggestions)
  capabilities_knowledge_ideas.register(app); capabilities_knowledge_capture.register(app); capabilities_knowledge_reviews.register(app)
  learning.register_learning_routes(app)
  app['capability_capture_inbox'].create('capture-learning-001', 'Original learning capture retained in the source history.')
  runner = web.AppRunner(app); await runner.setup()
  site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
  print(json.dumps({'api_port': site._server.sockets[0].getsockname()[1]}), flush=True)
  try: await asyncio.Event().wait()
  finally: await runner.cleanup(); store.db.close()

asyncio.run(main(sys.argv[1]))
`

async function startApi(origin: string): Promise<string> {
  const home = await mkdtemp(join(tmpdir(), 'gideon-personal-02-home-'))
  directories.push(home)
  const child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-c', nativeServer, origin], {
    cwd: root,
    env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: join(root, 'runtime') },
  })
  children.push(child)
  const line = await new Promise<string>((done, fail) => {
    let output = ''; let errors = ''
    const timeout = setTimeout(() => fail(new Error(`Native personal server did not start: ${errors}`)), 20000)
    child.stdout.on('data', chunk => { output += String(chunk); if (output.includes('\n')) { clearTimeout(timeout); done(output.split('\n')[0]) } })
    child.stderr.on('data', chunk => { errors += String(chunk) })
    child.once('exit', code => { clearTimeout(timeout); fail(new Error(`Native personal server exited ${code}: ${errors}`)) })
  })
  const ready = JSON.parse(line) as { api_port: number }
  return `http://127.0.0.1:${ready.api_port}`
}

describe('Personal Ideas and Learning native browser journey', () => {
  it('keeps cached suggestions as draft prompts and reviews native capture-backed proposals through the human gate', async () => {
    const port=await availablePort();const origin=`http://127.0.0.1:${port}`;const api=await startApi(origin)
    const entryDirectory=await mkdtemp(join(root,'apps/assistant/.personal-02-entry-'));directories.push(entryDirectory)
    const entryFile=join(entryDirectory,'personal-entry.tsx')
    await writeFile(entryFile, `import React from 'react';
import { createRoot } from 'react-dom/client';
import { ownerScope, readOwnerSession, readLoginStatus, signInOwner, OwnerSignIn } from '/src/shared/auth.web.tsx';
import { parseShellRoute, serializeShellRoute, createShellRoute } from '/src/shared/shell/shellRoutes.ts';
import { personalModuleDefinitions } from '/src/features/personal/moduleDefinitions.web.ts';

function Root() {
  const [identity, setIdentity] = React.useState(null);
  const [status, setStatus] = React.useState(null);
  const [error, setError] = React.useState('');
  const [route, setRoute] = React.useState(() => parseShellRoute(location.href, location.origin));
  const [moduleComponent, setModuleComponent] = React.useState(null);
  React.useEffect(() => {
    void readLoginStatus().then(setStatus);
    void readOwnerSession().then(setIdentity).catch(() => undefined);
    const pop = () => setRoute(parseShellRoute(location.href, location.origin));
    addEventListener('popstate', pop);
    return () => removeEventListener('popstate', pop);
  }, []);
  React.useEffect(() => {
    if (!identity || route.kind !== 'route') { setModuleComponent(null); return; }
    let active = true;
    const module = personalModuleDefinitions.find(candidate => candidate.matches(route));
    if (!module) { setModuleComponent(null); return () => { active = false; }; }
    const scope = ownerScope(location.origin, identity);
    void module.resolve(scope, route).then(async availability => {
      if (availability !== 'available') return null;
      return module.load();
    }).then(loaded => { if (active) setModuleComponent(() => loaded?.default ?? null); });
    return () => { active = false; };
  }, [identity, route]);
  const navigate = next => {
    history.pushState(null, '', serializeShellRoute(next));
    setRoute(parseShellRoute(location.href, location.origin));
  };
  if (!identity) return <OwnerSignIn status={status} error={error}
    onRetry={() => { void readOwnerSession().then(setIdentity).catch(() => undefined); }}
    onSignIn={async (username, password, token) => {
      try { await signInOwner(username, password, token); setIdentity(await readOwnerSession()); }
      catch (cause) { setError(String(cause)); }
    }} />;
  if (route.kind !== 'route') return null;
  const props = { route, scope: ownerScope(location.origin, identity), navigate, onReturn: () => navigate(createShellRoute('ideas')) };
  if (!moduleComponent) return <p role="status">Resolving personal workspace…</p>;
  return React.createElement(moduleComponent, props);
}

createRoot(document.getElementById('root')!).render(<Root />);
`, 'utf8')
    vite=await startViteEntryServer({root:join(root,'apps/assistant'),port,entryFile,apiOrigin:api})
    browser=await startBrowserHarness()
    await browser.navigate(`${origin}/assistant/ideas?v=1&view=workspace&placement=ideas`)
    await browser.waitFor("document.querySelector('#gideon-password')",'real owner sign-in form')
    await browser.evaluate(`(()=>{const set=(id,value)=>{const input=document.getElementById(id);Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))};set('gideon-username','personal-owner');set('gideon-password','native-owner-password');document.querySelector('form').requestSubmit();return true})()`)
    await browser.waitFor("document.body.innerText.includes('Build a source-linked learning plan')",'saved Idea')
    const nativeIdea = await browser.evaluate<string>("document.querySelector('.gideon-ideas__card')?.innerText || ''")
    expect(nativeIdea).toContain('No accept or dismiss decision is recorded');expect(nativeIdea).toContain('Explore a grounded next step')
    expect(await browser.evaluate<boolean>("document.querySelector('.gideon-ideas__record-details')?.open === false")).toBe(true)
    await browser.evaluate("document.querySelector('.gideon-ideas__record-details summary')?.click()")
    const provenance = await browser.evaluate<string>("document.querySelector('.gideon-ideas__record-details')?.innerText || ''")
    expect(provenance).toContain('Source kind');expect(provenance).toContain('knowledge-idea-list');expect(provenance).toContain('Source ID')
    await browser.evaluate("document.querySelector('.gideon-ideas__record-details summary')?.click()")
    await browser.evaluate("history.pushState(null,'',location.pathname+'?v=1&view=detail&recordKind=idea&recordId=50d3e623-0be4-4e04-8ca9-68109c5c1267&placement=ideas&from=ideas&fromPlacement=ideas');dispatchEvent(new PopStateEvent('popstate'))")
    await browser.waitFor("document.querySelector('.gideon-personal-record h2')?.innerText === 'Native saved ideas'",'record-aware Idea detail')
    expect(await browser.evaluate<boolean>("document.querySelector('.gideon-personal-record details')?.open === false")).toBe(true)
    await browser.evaluate("document.querySelector('.gideon-personal-record summary')?.click()")
    expect(await browser.evaluate<string>("document.querySelector('.gideon-personal-record details')?.innerText || ''")).toContain('50d3e623-0be4-4e04-8ca9-68109c5c1267')
    expect(await browser.evaluate<string>("new URLSearchParams(location.search).get('from') || ''")).toBe('ideas')
    await browser.evaluate("history.pushState(null,'',location.pathname+'?v=1&view=workspace&placement=ideas');dispatchEvent(new PopStateEvent('popstate'))")
    await browser.waitFor("document.querySelector('.gideon-ideas__card')",'Ideas list after detail return')
    await browser.evaluate("Array.from(document.querySelectorAll('.gideon-ideas__chips button')).find(button=>button.innerText.includes('cached prompt'))?.click()")
    expect(await browser.evaluate<string>("document.getElementById('gideon-ideas-draft').value")).toBe('Use this cached prompt in a draft')
    expect(await browser.evaluate<string>("document.querySelector('.gideon-ideas__card').innerText")).not.toContain('Use this cached prompt in a draft')

    await browser.evaluate("history.pushState(null,'',location.pathname+'?v=1&view=workspace&placement=capabilities%2Fknowledge%2Fideas');dispatchEvent(new PopStateEvent('popstate'))")
    await browser.waitFor("document.body.innerText.includes('Loading saved Ideas')",'changed Ideas placement loading state')
    const switchedView = await browser.evaluate<{ draft: string; savedCards: number }>("({draft:document.getElementById('gideon-ideas-draft')?.value||'',savedCards:document.querySelectorAll('.gideon-ideas__card').length})")
    expect(switchedView).toEqual({ draft: '', savedCards: 0 })
    await browser.waitFor("document.querySelector('.gideon-ideas__card')",'Ideas refresh after placement change')
    await browser.evaluate("history.pushState(null,'',location.pathname+'?v=1&view=workspace&placement=ideas');dispatchEvent(new PopStateEvent('popstate'))")
    await browser.waitFor("document.querySelector('.gideon-ideas__card')",'saved Ideas after return')

    await browser.evaluate(`history.pushState(null,'',location.pathname+'?v=1&view=workspace&placement=learning');dispatchEvent(new PopStateEvent('popstate'))`)
    await browser.waitFor("document.body.innerText.includes('Original learning capture retained in the source history.')",'capture history')
    await browser.waitFor("document.body.innerText.includes('Review skill proposal')",'proposal inbox')
    expect(await browser.evaluate<boolean>("document.querySelectorAll('.gideon-learning pre').length === 0")).toBe(true)
    expect(await browser.evaluate<boolean>("Array.from(document.querySelectorAll('.gideon-learning__data')).every(panel => panel.innerText.includes('Retry') || panel.querySelector('dl, ul, p'))")).toBe(true)
    expect(await browser.evaluate<boolean>("document.body.innerText.includes('Capture error')")).toBe(false)
    await browser.evaluate("Array.from(document.querySelectorAll('.gideon-learning__proposal')).find(button=>button.innerText.includes('Review skill proposal'))?.click()")
    await browser.waitFor("document.body.innerText.includes('A proposal body backed by a real local test record.')",'native proposal detail')
    expect(await browser.evaluate<boolean>("document.body.innerText.includes('Only the authenticated human reviewer can decide a pending proposal')")).toBe(true)
    await browser.evaluate("document.querySelector('.gideon-learning__ack input').click()")
    const dismiss=await browser.evaluate<boolean>("Array.from(document.querySelectorAll('.gideon-learning__decision button')).find(button=>button.innerText.includes('Dismiss'))?.disabled")
    expect(dismiss).toBe(false)
    await browser.evaluate("Array.from(document.querySelectorAll('.gideon-learning__decision button')).find(button=>button.innerText.includes('Dismiss'))?.click()")
    await browser.waitFor("!document.querySelector('.gideon-learning__review')",'proposal detail closes after dismissal')
    await browser.waitFor("document.querySelector('[aria-labelledby=\\\"learning-proposals\\\"]')?.innerText.includes('No pending proposals.')",'empty proposal inbox affordance')
    const emptyInbox = await browser.evaluate<string>("document.querySelector('[aria-labelledby=\\\"learning-proposals\\\"]')?.innerText || ''")
    expect(emptyInbox).toContain('0 pending')
    expect(emptyInbox).toContain('No pending proposals.')
    await browser.navigate(`${origin}/assistant/ideas?v=1&view=workspace&placement=learning`)
    await browser.waitFor("document.body.innerText.includes('Original learning capture retained in the source history.')",'Learning after reload')
    await browser.waitFor("document.querySelector('[aria-labelledby=\\\"learning-proposals\\\"]')?.innerText.includes('No pending proposals.')",'durable dismissed-proposal projection')
    const durableInbox = await browser.evaluate<string>("document.querySelector('[aria-labelledby=\\\"learning-proposals\\\"]')?.innerText || ''")
    expect(durableInbox).toContain('0 pending')
    expect(durableInbox).toContain('No pending proposals.')
    expect(durableInbox).not.toContain('Review skill proposal')
    expect(browser.diagnostics().filter(message => !message.includes('Download the React DevTools for a better development experience:') && !message.includes('net::ERR_ABORTED'))).toEqual([])
  }, 120000)
})
