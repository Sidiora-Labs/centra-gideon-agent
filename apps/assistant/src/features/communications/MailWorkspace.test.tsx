import { afterAll, describe, expect, it } from 'vitest'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { startBrowserHarness, startViteEntryServer, type BrowserHarness } from '../../../test-support/browserHarness'
import { filterMailItems, mailDraftRouteId, unreadMailItems } from './MailWorkspace.web'
import { providerItemKey, type CommunicationItem, type MirrorMessage, type OutboundDraftItem } from './types'

const ownerScopeKey = JSON.stringify(['https://gideon.local', 'owner-1'])
const root = resolve(process.cwd(), '../..')
const children: ChildProcessWithoutNullStreams[] = []
const directories: string[] = []
let vite: Awaited<ReturnType<typeof startViteEntryServer>> | undefined
let browser: BrowserHarness | undefined
let currentPhase = 'starting native Mail test services'
let browserStep = 0

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

async function evaluate<T = unknown>(_send: BrowserHarness['command'], expression: string): Promise<T> {
  if (!browser) throw new Error('Chromium browser harness is not running')
  const step = ++browserStep
  try {
    return await browser.evaluate<T>(expression)
  } catch (error) {
    throw new Error(`Mail browser step ${step} failed during ${currentPhase}: ${String(error)}`)
  }
}

async function waitFor(_send: BrowserHarness['command'], expression: string): Promise<void> {
  if (!browser) throw new Error('Chromium browser harness is not running')
  const step = ++browserStep
  try {
    await browser.waitFor(expression, `Mail browser step ${step} during ${currentPhase}`)
  } catch (error) {
    throw new Error(`Mail browser step ${step} failed during ${currentPhase}: ${String(error)}`)
  }
}

async function acceptNextJavascriptDialog(send: BrowserHarness['command']): Promise<void> {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      await send('Page.handleJavaScriptDialog', { accept: true })
      return
    } catch (error) {
      if (!String(error).includes('No dialog is showing')) throw error
      await new Promise(resolve => setTimeout(resolve, 100))
    }
  }
  throw new Error('Mail send confirmation did not open in Chromium')
}

function message(externalId: string, subject: string, sender: string): CommunicationItem<MirrorMessage> {
  return {
    identity: { ownerScopeKey, accountId: 'native-account-1', providerKind: 'mail-mirror', sourceKind: 'mail-mirror-message', nativeId: externalId },
    readiness: 'read_only', freshness: 'imported', allowedActions: ['open', 'read'],
    value: {
      external_id: externalId, thread_id: `thread:${externalId}`, occurred_at: '2026-09-26T09:30:00Z',
      direction: 'inbound', subject, body: 'The integration record is available for review.',
      sender: [sender], recipients: ['owner@example.test'], attachments: [], source_digest: 'digest', is_read: false, read_state: 'local_only',
    },
  }
}

function draft(accountId: string): OutboundDraftItem {
  const value = {
    id: 'native-draft-9', request_key: 'request-9', account_id: accountId, account_revision: 4,
    credential_ref: 'credential-ref', sender: 'owner@example.test', to: ['recipient@example.test'],
    subject: 'Review', body: 'Exact reviewed content', source_message_id: null, attachments: [],
    message_id: '<native-message@example.test>', created_at: '2026-09-26T09:30:00Z', updated_at: '2026-09-26T09:30:00Z',
    state: 'draft' as const, provider_acceptance: 'not_submitted', delivery: 'not_submitted', verification: null,
    content_sha256: 'sha256-native-content', fingerprint: 'sha256-fingerprint', revision: 3,
  }
  return {
    identity: { ownerScopeKey, accountId, providerKind: 'mail-mirror', sourceKind: 'outbound-email-draft', nativeId: value.id },
    readiness: 'configured', freshness: 'current', allowedActions: ['edit', 'review'], value,
  }
}

describe('mail workspace contracts', () => {
  it('searches only the selected mailbox fields and preserves list order', () => {
    const records = [message('native-1', 'Quarterly plan', 'finance@example.test'), message('native-2', 'Release notes', 'team@example.test')]
    const read = { ...records[1], value: { ...records[1].value, is_read: true } }
    expect(unreadMailItems([records[0], read])).toEqual([records[0]])
    expect(filterMailItems(records, ' FINANCE@EXAMPLE.TEST ')).toEqual([records[0]])
    expect(filterMailItems(records, 'release').map(item => item.identity.nativeId)).toEqual(['native-2'])
    expect(filterMailItems(records, 'not present')).toEqual([])
  })

  it('routes identical native draft IDs separately by native account under one owner', () => {
    const first = draft('native-account-1')
    const second = draft('native-account-2')
    expect(first.value.id).toBe(second.value.id)
    expect(mailDraftRouteId(first)).not.toBe(mailDraftRouteId(second))
    expect(JSON.parse(mailDraftRouteId(first))).toEqual([ownerScopeKey, 'native-account-1', 'mail-mirror', 'outbound-email-draft', 'native-draft-9'])
  })

  it('keeps the same provider message ID distinct across two native mailbox accounts', () => {
    const first = message('provider-message-42', 'First mailbox', 'a@example.test')
    const second = { ...message('provider-message-42', 'Second mailbox', 'b@example.test'), identity: {
      ...message('provider-message-42', 'Second mailbox', 'b@example.test').identity, accountId: 'native-account-2',
    } }
    expect(first.identity.nativeId).toBe(second.identity.nativeId)
    expect(providerItemKey(first.identity)).not.toBe(providerItemKey(second.identity))
  })

  it('resolves real native auth denial and missing mailbox responses', async () => {
    const python = process.env.GIDEON_TEST_PYTHON || 'python3'
    const apiDirectory = await mkdtemp(join(tmpdir(), 'gideon-mail-resolver-'))
    directories.push(apiDirectory)
    const serverSource = String.raw`
import asyncio, json, os, sys
from pathlib import Path
from aiohttp import web
import gideon.core.config.loader as loader
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers import auth
from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.security.auth import credentials
from gideon.workspace.capabilities.communications import PeopleStore, mirrors

async def main(origin):
  home=Path(os.environ['GIDEON_HOME']); home.mkdir(parents=True,exist_ok=True)
  loader.config_dir=lambda: home; credentials.config_dir=lambda: home
  (home/'config.json').write_text(json.dumps({'auth': {'login_enabled': True}, 'providers': []}),encoding='utf-8')
  credentials.set_password('communications-owner','correct-horse-battery-staple')
  token_auth.use_persistent_secret(); token_auth.revoke_all_sessions()
  store=PeopleStore(root=home/'capabilities'/'communications')
  account=mirrors.save_account(store,{'name':'Resolver mailbox','kind':'maildir','alias':'custom','owner_email':'owner@example.test'})
  app=web.Application(middlewares=[token_auth.token_auth_middleware(port=10000)])
  app['port']=10000; app['allowed_origins']={origin}
  app.router.add_get('/api/auth/status',auth.api_login_status)
  app.router.add_get('/api/auth/session',auth.api_auth_session)
  app.router.add_post('/api/auth/login',auth.api_auth_login)
  app.router.add_post('/api/auth/logout',auth.api_auth_logout)
  register(app)
  runner=web.AppRunner(app); await runner.setup()
  site=web.TCPSite(runner,'127.0.0.1',0); await site.start()
  port=site._server.sockets[0].getsockname()[1]
  print(json.dumps({'port':port,'account':account['id']}),flush=True)
  try: await asyncio.Event().wait()
  finally: await runner.cleanup()

asyncio.run(main(sys.argv[1]))
`
    const script = join(apiDirectory, 'resolver_server.py')
    await writeFile(script, serverSource)
    const child = spawn(python, [script, 'http://127.0.0.1:4178'], { env: {
      ...process.env, GIDEON_HOME: join(apiDirectory, 'home'), PYTHONPATH: join(root, 'runtime'), GIDEON_TEST_MODEL: '',
    } })
    children.push(child)
    const server = await new Promise<{ port: number; account: string }>((done, fail) => {
      let stdout = ''; let stderr = ''
      const timeout = setTimeout(() => fail(new Error(`Native Mail resolver server start timed out: ${stderr}`)), 15000)
      child.stdout.on('data', chunk => { stdout += String(chunk); if (stdout.includes('\n')) { clearTimeout(timeout); done(JSON.parse(stdout.split('\n')[0])) } })
      child.stderr.on('data', chunk => { stderr += String(chunk) })
      child.once('exit', code => { clearTimeout(timeout); fail(new Error(`Native Mail resolver server exited ${code}: ${stderr}`)) })
    })
    const entryDirectory = await mkdtemp(join(root, 'apps/assistant/.mail-resolver-browser-'))
    directories.push(entryDirectory)
    const entry = join(entryDirectory, 'resolver-entry.ts')
    await writeFile(entry, `
      import { ownerScope } from '../src/shared/auth.web'
      import { createShellRoute } from '../src/shared/shell/shellRoutes'
      import { communicationsModuleDefinitions } from '../src/features/communications/moduleDefinitions.web'
      window.__resolveMail = async account => {
        const scope = ownerScope(location.origin, { user: 'communications-owner' })
        const route = createShellRoute('apps', { placement: { id: 'capabilities/communications/outbound', query: { account } } })
        return communicationsModuleDefinitions[0].resolve!(scope, route)
      }
    `)
    vite = await startViteEntryServer({ root: join(root, 'apps/assistant'), port: 4178, entryFile: entry,
      apiOrigin: `http://127.0.0.1:${server.port}` })
    browser = await startBrowserHarness()
    await browser.navigate('http://127.0.0.1:4178/assistant/apps?v=1')
    await browser.waitFor(`typeof window.__resolveMail === 'function'`, 'Native Mail resolver entry did not load')
    const login = await browser.evaluate<number>(`fetch('/api/auth/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'},body:JSON.stringify({username:'communications-owner',password:'correct-horse-battery-staple',totp:''})}).then(response=>response.status)`)
    expect(login).toBe(200)
    expect(await browser.evaluate(`window.__resolveMail(${JSON.stringify(server.account)})`)).toBe('available')
    expect(await browser.evaluate(`window.__resolveMail('nonexistent-native-account')`)).toBe('missing')
    const logout = await browser.evaluate<number>(`fetch('/api/auth/logout',{method:'POST',credentials:'same-origin',headers:{'X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'}}).then(response=>response.status)`)
    expect(logout).toBe(200)
    expect(await browser.evaluate(`window.__resolveMail(${JSON.stringify(server.account)})`)).toBe('denied')
  }, 45000)

  it('drives authenticated native mail, local read state and send-once draft review in Chromium', async () => {
    currentPhase = 'starting real auth, Maildir, IMAP, artifact, and loopback SMTP services'
    const python = process.env.GIDEON_TEST_PYTHON || 'python3'
    const serverSource = String.raw`
import asyncio, json, os, socket, socketserver, sys, threading
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import gideon.core.config.loader as loader
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers import auth
from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.interfaces.dashboard.handlers.capabilities_communications_outbound import register as register_outbound
from gideon.core.config.credentials import save_credential
from gideon.security.auth import credentials
from gideon.workspace.capabilities.communications import PeopleStore, mirrors
from gideon.workspace.capabilities.communications.outbound_email import OutboundEmail, SMTPTransport
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.artifacts.registry import register_provider
from gideon.workspace.artifacts.handlers import register_artifact_routes

async def main(origin):
  home=Path(os.environ['GIDEON_HOME'])
  home.mkdir(parents=True,exist_ok=True)
  loader.config_dir=lambda: home
  credentials.config_dir=lambda: home
  (home/'config.json').write_text(json.dumps({'auth': {'login_enabled': True}, 'providers': []}), encoding='utf-8')
  os.environ['GIDEON_CREDENTIAL_BACKEND']='dotenv'
  save_credential('TEST_MAIL_CREDENTIAL','loopback-only-fixture-credential')
  credentials.set_password('communications-owner','correct-horse-battery-staple')
  token_auth.use_persistent_secret(); token_auth.revoke_all_sessions()
  store=PeopleStore(root=home/'capabilities'/'communications')
  local=mirrors.save_account(store, {'name':'Native Maildir','kind':'maildir','alias':'custom','owner_email':'owner@example.test'})
  mirrors.upload(store,local['id'],{'folder':'INBOX','content':'From: Sender <sender@example.test>\nTo: Owner <owner@example.test>\nSubject: Native fixture message\nDate: Sat, 26 Sep 2026 09:30:00 +0000\nMessage-ID: <native-message-7@example.test>\n\nRead this imported message from the real mirror handler.\n'})
  mirrors.sync(store,local['id'])
  imap=mirrors.save_account(store, {'name':'Draft test account','kind':'imap','alias':'custom','owner_email':'owner@example.test','host':'localhost','username':'owner@example.test','credential_ref':'TEST_MAIL_CREDENTIAL'})
  reply_raw=b'From: Sender <sender@example.test>\nTo: Owner <owner@example.test>\nSubject: Native reply fixture\nDate: Sat, 26 Sep 2026 09:30:00 +0000\nMessage-ID: <native-reply-8@example.test>\n\nReply to this imported message in the selected IMAP account.\n'
  reply_message=mirrors.normalize_message(reply_raw,'owner@example.test','reply-fixture')
  with store.connect() as db, db:
    mirrors.schema(db)
    db.execute('INSERT INTO mirror_messages VALUES (?,?,?)',(imap['id'],reply_message['external_id'],json.dumps(reply_message)))
  sink = {'messages': [], 'connections': []}
  class Handler(socketserver.StreamRequestHandler):
    def handle(self):
      sink['connections'].append(self.client_address)
      eol=bytes((13,10))
      self.wfile.write(b'220 local contract SMTP'+eol)
      data=False; payload=bytearray()
      while True:
        line=self.rfile.readline()
        if not line: return
        value=line.decode('utf-8',errors='replace').rstrip('\r\n').upper()
        if data:
          if value=='.':
            sink['messages'].append(bytes(payload)); data=False; self.wfile.write(b'250 accepted locally'+eol)
          else: payload.extend(line[1:] if line.startswith(b'..') else line)
        elif value.startswith(('EHLO ','HELO ')): self.wfile.write(b'250-local'+eol+b'250 SIZE 20971520'+eol)
        elif value.startswith(('MAIL FROM:','RCPT TO:')): self.wfile.write(b'250 ok'+eol)
        elif value=='DATA': data=True; self.wfile.write(b'354 end with dot'+eol)
        elif value=='QUIT': self.wfile.write(b'221 bye'+eol); return
        else: self.wfile.write(b'250 ok'+eol)
  smtp=socketserver.ThreadingTCPServer(('127.0.0.1',0),Handler)
  smtp_thread=threading.Thread(target=smtp.serve_forever,daemon=True); smtp_thread.start()
  with socket.create_connection(smtp.server_address,timeout=2) as probe:
    stream=probe.makefile('rb')
    banner=stream.readline().decode('ascii','replace').strip()
    if not banner.startswith('220 '): raise RuntimeError(f'SMTP listener on {smtp.server_address} returned {banner!r}')
    probe.sendall(b'EHLO fixture.local\r\n')
    replies=[]
    while True:
      reply=stream.readline().decode('ascii','replace').strip()
      replies.append(reply)
      if not reply.startswith('250-'): break
    if not replies[-1].startswith('250 '): raise RuntimeError(f'SMTP listener on {smtp.server_address} rejected EHLO: {replies!r}')
    probe.sendall(b'QUIT\r\n')
    quit_reply=stream.readline().decode('ascii','replace').strip()
    if not quit_reply.startswith('221 '): raise RuntimeError(f'SMTP listener on {smtp.server_address} rejected QUIT: {quit_reply!r}')
  artifacts=NativeArtifactProvider(home/'artifacts')
  artifacts.create(name='Reviewed attachment',content='Real native artifact fixture',kind='text')
  register_provider(artifacts)
  outbound=OutboundEmail(store, artifacts=artifacts,
    transport=SMTPTransport(host='127.0.0.1',port=smtp.server_address[1],starttls=False,authenticate=False))
  read_gate_started=asyncio.Event(); read_gate_release=asyncio.Event()
  @web.middleware
  async def hold_read_state(request, handler):
    if request.method == 'PATCH' and request.path.endswith('/read-state'):
      read_gate_started.set(); await read_gate_release.wait()
    return await handler(request)
  app=web.Application(middlewares=[token_auth.token_auth_middleware(port=10000), hold_read_state])
  app['port']=10000; app['allowed_origins']={origin}
  app.router.add_get('/api/auth/status',auth.api_login_status)
  app.router.add_get('/api/auth/session',auth.api_auth_session)
  app.router.add_post('/api/auth/login',auth.api_auth_login)
  app.router.add_post('/api/auth/logout',auth.api_auth_logout)
  async def smtp_count(_request): return web.json_response({'count':len(sink['messages'])})
  async def smtp_status(_request):
    target=(outbound.transport.host,outbound.transport.port)
    status={'configured':target,'bound':smtp.server_address,'thread_alive':smtp_thread.is_alive(),'listener_fd':smtp.fileno(),'accepted_connections':len(sink['connections']),'messages':len(sink['messages']),'reachable':False}
    try:
      with socket.create_connection(target,timeout=2) as probe:
        probe.settimeout(2)
        stream=probe.makefile('rb')
        status['probe_banner']=stream.readline().decode('ascii','replace').strip()
        probe.sendall(b'EHLO status.local\r\n')
        replies=[]
        while True:
          reply=stream.readline().decode('ascii','replace').strip(); replies.append(reply)
          if not reply.startswith('250-'): break
        status['probe_ehlo']=replies
        probe.sendall(b'QUIT\r\n')
        status['probe_quit']=stream.readline().decode('ascii','replace').strip()
        status['reachable']=status['probe_banner'].startswith('220 ') and replies[-1].startswith('250 ') and status['probe_quit'].startswith('221 ')
    except (OSError, TimeoutError) as exc:
      status['probe_error']=str(exc)
    return web.json_response(status)
  async def read_gate_status(_request): return web.json_response({'started':read_gate_started.is_set()})
  async def release_read_gate(_request): read_gate_release.set(); return web.json_response({'released':True})
  app.router.add_get('/api/test/smtp-count',smtp_count)
  app.router.add_get('/api/test/smtp-status',smtp_status)
  app.router.add_get('/api/test/read-gate',read_gate_status)
  app.router.add_post('/api/test/read-gate/release',release_read_gate)
  register_outbound(app,outbound); register(app); register_artifact_routes(app)
  runner=web.AppRunner(app); await runner.setup()
  site=web.TCPSite(runner,'127.0.0.1',0); await site.start()
  port=site._server.sockets[0].getsockname()[1]
  route_probe=web.Application()
  register_outbound(route_probe,outbound); register(route_probe)
  async with TestClient(TestServer(route_probe)) as client:
    base='/api/capabilities/communications/outbound-email/drafts'
    created=await client.post(base,json={'request_key':'route-binding-proof','account_id':imap['id'],'to':['route-proof@example.test'],'subject':'Route binding proof','body':'Accepted through the registered outbound handler.'})
    if created.status != 201: raise RuntimeError(f'Outbound route proof draft returned {created.status}: {await created.text()}')
    proof=(await created.json())['draft']
    approved=await client.post(base+'/'+proof['id']+'/approve',json={'revision':proof['revision'],'content_sha256':proof['content_sha256'],'confirm_exact':True})
    if approved.status != 200: raise RuntimeError(f'Outbound route proof approval returned {approved.status}: {await approved.text()}')
    approved_row=(await approved.json())['draft']
    sent=await client.post(base+'/'+proof['id']+'/send',json={'revision':approved_row['revision'],'content_sha256':approved_row['content_sha256'],'confirm_send':True})
    if sent.status != 200: raise RuntimeError(f'Outbound route proof send returned {sent.status}: {await sent.text()}')
    accepted=(await sent.json())['draft']
    if accepted['state'] != 'accepted' or len(sink['messages']) != 1 or b'Route binding proof' not in sink['messages'][0]:
      raise RuntimeError(f'Outbound route did not use its configured loopback service: state={accepted.get("state")!r}, messages={len(sink["messages"])}')
  with store.connect() as db, db:
    db.execute('DELETE FROM outbound_email_drafts WHERE id=?',(proof['id'],))
  sink['messages'].clear()
  print(json.dumps({'api_port':port,'maildir_account':local['id'],'imap_account':imap['id'],'reply_message_id':reply_message['external_id'],'smtp_host':smtp.server_address[0],'smtp_port':smtp.server_address[1],'smtp_preflight':{'banner':banner,'ehlo':replies,'quit':quit_reply}}),flush=True)
  try: await asyncio.Event().wait()
  finally:
    await runner.cleanup(); smtp.shutdown(); smtp.server_close(); smtp_thread.join(timeout=2)

asyncio.run(main(sys.argv[1]))
`
    async function startApi(origin: string, directory: string) {
      const script = join(directory, 'mail_server.py')
      await import('node:fs/promises').then(fs => fs.writeFile(script, serverSource))
      const child = spawn(python, [script, origin], { env: { ...process.env, GIDEON_HOME: join(directory, 'home'),
        PYTHONPATH: join(root, 'runtime'), GIDEON_TEST_MODEL: '' } })
      children.push(child)
      const output = await new Promise<string>((done, fail) => {
        let stdout = ''; let stderr = ''
        const timeout = setTimeout(() => fail(new Error(`Mail handler server start timed out: ${stderr}`)), 15000)
        child.stdout.on('data', chunk => { stdout += String(chunk); if (stdout.includes('\n')) { clearTimeout(timeout); done(stdout.split('\n')[0]) } })
        child.stderr.on('data', chunk => { stderr += String(chunk) })
        child.once('exit', code => { clearTimeout(timeout); fail(new Error(`Mail handler server exited ${code}: ${stderr}`)) })
      })
      return JSON.parse(output) as { api_port: number; maildir_account: string; imap_account: string; reply_message_id: string; smtp_host: string; smtp_port: number; smtp_preflight: { banner: string; ehlo: string[]; quit: string } }
    }
    const port = await availablePort()
    const origin = `http://127.0.0.1:${port}`
    const directory = await mkdtemp(join(tmpdir(), 'gideon-mail-browser-'))
    directories.push(directory)
    const { api_port: apiPort, maildir_account: maildirId, imap_account: imapId, reply_message_id: replyMessageId, smtp_host: smtpHost, smtp_port: smtpPort, smtp_preflight: smtpPreflight } = await startApi(origin, directory)
    expect({ smtpHost, smtpPort, smtpPreflight }).toMatchObject({ smtpHost: '127.0.0.1', smtpPort: expect.any(Number), smtpPreflight: { banner: expect.stringMatching(/^220 /), ehlo: expect.arrayContaining([expect.stringMatching(/^250 /)]), quit: expect.stringMatching(/^221 /) } })
    const entryDirectory = await mkdtemp(join(root, 'apps/assistant/.mail-workspace-browser-'))
    directories.push(entryDirectory)
    const entry = join(entryDirectory, 'mail-entry.tsx')
    await writeFile(entry, `
      import { flushSync } from 'react-dom'
      import { createRoot } from 'react-dom/client'
      import { ownerScope, readOwnerSession } from '../src/shared/auth.web'
      import { createShellRoute, parseShellRoute, serializeShellRoute } from '../src/shared/shell/shellRoutes'
      import { ShellThemeProvider } from '../src/shared/shell/shellTheme.web'
      import MailWorkspace from '../src/features/communications/MailWorkspace.web'
      import { createCommunicationClient } from '../src/features/communications/communicationClient'
      const root = createRoot(document.getElementById('root')!)
      const render = (route: ReturnType<typeof parseShellRoute>) => {
        window.__route = route
        root.render(<ShellThemeProvider>{route.destination === 'apps'
          ? <MailWorkspace route={route} scope={window.__scope} navigate={next => {
            history.pushState({}, '', serializeShellRoute(next)); render(next)
          }} onReturn={() => {
            const back = route.returnTo
            const next = back ? createShellRoute(back.destination, { view: back.record ? 'detail' : 'list', record: back.record,
              placement: back.placement, sessionId: back.sessionId }) : createShellRoute('apps', { placement: route.placement })
            history.pushState({}, '', serializeShellRoute(next)); render(next)
          }} returnTo={route.returnTo} />
          : <main data-returned-to={route.destination}>Returned to {route.destination} {route.sessionId || ''}</main>}
        </ShellThemeProvider>)
      }
      window.__startMail = async () => {
        const owner = await readOwnerSession()
        window.__scope = ownerScope(location.origin, owner)
        render(parseShellRoute(location.href, location.origin))
        return true
      }
      window.__navigate = (route: ReturnType<typeof parseShellRoute>) => {
        history.pushState({}, '', serializeShellRoute(route)); render(route)
      }
      window.__changeOwnerScope = () => {
        window.__scope = { ...window.__scope, cacheKey: JSON.stringify([window.__scope.cacheKey, 'next-owner']) }
        flushSync(() => render(window.__route))
      }
      window.__testMailAccessRevocation = async (accountId: string) => {
        const client = createCommunicationClient(window.__scope)
        const account = (await client.readMirrorAccounts()).find(row => row.id === accountId)
        if (!account) throw new Error('Mail fixture account is unavailable')
        client.selectConnection(accountId, 'mail-mirror')
        const initial = await client.readMirrorMessages(account)
        await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin', headers: { 'X-Gideon-API-Version': '1', 'X-Session-Key': 'dashboard:ui' } })
        try {
          const denied = await client.readMirrorMessages(account)
          return { initialCount: initial.value.length, returnedStale: denied.freshness === 'stale', status: 0 }
        } catch (error) {
          const denied = error as { status?: number; authRequired?: boolean }
          return {
            initialCount: initial.value.length,
            returnedStale: false,
            status: denied.status || 0,
            authRequired: denied.authRequired === true,
          }
        }
      }
      readOwnerSession().then(owner => {
        window.__scope = ownerScope(location.origin, owner)
        render(parseShellRoute(location.href, location.origin))
      }).catch(() => {})
    `)
    vite = await startViteEntryServer({ root: join(root, 'apps/assistant'), port, entryFile: entry, apiOrigin: `http://127.0.0.1:${apiPort}` })
    browser = await startBrowserHarness()
    const send = browser.command
    currentPhase = 'loading the authenticated Mail workspace'
    await browser.navigate(`${origin}/assistant/apps?v=1&placement=capabilities%2Fcommunications%2Foutbound&q.account=${encodeURIComponent(maildirId)}`)
    await waitFor(send, `typeof window.__startMail === 'function'`)
    const login = await evaluate(send, `fetch('/api/auth/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'},body:JSON.stringify({username:'communications-owner',password:'correct-horse-battery-staple',totp:''})}).then(response=>response.status)`)
    expect(login).toBe(200)
    currentPhase = 'checking mailbox selection, owner isolation, search, and local read state'
    await evaluate(send, `window.__startMail()`)
    await waitFor(send, `document.querySelector('[aria-label="Mailbox account"]') && document.body.innerText.includes('Native fixture message')`)
    await evaluate(send, `(()=>{window.__navigate({...window.__route,placement:{id:'capabilities/communications/outbound',query:{account:'missing-account'}}});return true})()`)
    await waitFor(send, `document.body.innerText.includes('Mailbox account unavailable') && document.querySelector('[aria-label="Choose mailbox account"]')`)
    const oldOwnerPickerVisible = await evaluate(send, `(()=>{window.__changeOwnerScope();return Boolean(document.querySelector('[aria-label="Choose mailbox account"]'))})()`)
    expect(oldOwnerPickerVisible).toBe(false)
    await evaluate(send, `window.__startMail()`)
    await evaluate(send, `(()=>{window.__navigate({...window.__route,placement:{id:'capabilities/communications/outbound',query:{account:${JSON.stringify(maildirId)}}}});return true})()`)
    await waitFor(send, `document.querySelector('[aria-label="Mailbox account"]') && document.body.innerText.includes('Native fixture message')`)
    await evaluate(send, `(()=>{const input=document.querySelector('[aria-label="Search mail"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'native fixture');input.dispatchEvent(new Event('input',{bubbles:true}));return true})()`)
    await waitFor(send, `document.querySelectorAll('[aria-label="Messages"] button').length===1`)
    await evaluate(send, `(()=>{const input=document.querySelector('[aria-label="Search mail"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'no matching message');input.dispatchEvent(new Event('input',{bubbles:true}));return true})()`)
    await waitFor(send, `document.body.innerText.includes('No messages match this search')`)
    await evaluate(send, `(()=>{const input=document.querySelector('[aria-label="Search mail"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'');input.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[aria-label="Mailbox views"] button:nth-child(2)').click();return true})()`)
    await waitFor(send, `document.querySelector('[aria-label="Messages"] button')`)
    await evaluate(send, `document.querySelector('[aria-label="Messages"] button').click()`)
    await waitFor(send, `document.querySelector('#mail-detail-title')?.innerText==='Native fixture message'`)
    await evaluate(send, `document.querySelector('button').click()`)
    await waitFor(send, `document.body.innerText.includes('Unread is tracked locally in Gideon')`)
    const accountSelector = `document.querySelector('[aria-label="Mailbox account"]')`
    currentPhase = 'switching to the real IMAP mailbox and creating a reply draft'
    await evaluate(send, `(()=>{const select=${accountSelector};Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,${JSON.stringify(imapId)});select.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await waitFor(send, `document.querySelector('[aria-label="Mailbox account"]')?.value===${JSON.stringify(imapId)} && document.body.innerText.includes('Native reply fixture')`)
    await evaluate(send, `document.querySelector('[aria-label="Messages"] button').click()`)
    await waitFor(send, `document.querySelector('#mail-detail-title')?.innerText==='Native reply fixture'`)
    const replyDisabled = await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText.includes('Reply with a governed draft'))?.disabled`)
    expect(replyDisabled).toBe(false)
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText.includes('Reply with a governed draft')).click()`)
    await waitFor(send, `document.querySelector('#compose-heading')?.innerText==='Reply draft'`)
    const replyPrefill = await evaluate(send, `(()=>{const composer=document.querySelector('#compose-heading')?.parentElement;const labels=[...composer.querySelectorAll('label')];return {to:labels.find(node=>node.textContent.startsWith('To'))?.querySelector('input')?.value,subject:labels.find(node=>node.textContent.startsWith('Subject'))?.querySelector('input')?.value}})()`)
    expect(replyPrefill).toEqual({ to: 'sender@example.test', subject: 'Re: Native reply fixture' })
    await evaluate(send, `(()=>{const field=[...document.querySelectorAll('label')].find(node=>node.textContent.startsWith('Message')).querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(field,'A reply tied to the selected mailbox record.');field.dispatchEvent(new Event('input',{bubbles:true}));return true})()`)
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText==='Save and review').click()`)
    await waitFor(send, `document.querySelector('#mail-detail-title')?.innerText==='Re: Native reply fixture' && document.body.innerText.includes('A reply tied to the selected mailbox record.')`)
    const replySource = await evaluate(send, `fetch('/api/capabilities/communications/outbound-email/drafts',{headers:{'X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'}}).then(response=>response.json()).then(result=>result.drafts.find(row=>row.subject==='Re: Native reply fixture')?.source_message_id)`)
    expect(replySource).toBe(replyMessageId)
    currentPhase = 'composing against the real artifact picker and saving a reviewed draft'
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText.includes('Back to messages')).click()`)
    await waitFor(send, `document.querySelector('[aria-label="Mailbox account"]')?.value===${JSON.stringify(imapId)} && document.querySelector('[aria-label="Messages"]')`)
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText==='Compose').click()`)
    await waitFor(send, `document.querySelector('#compose-heading')`)
    await evaluate(send, `(()=>{const set=(label,value)=>{const field=[...document.querySelectorAll('label')].find(node=>node.textContent.startsWith(label)).querySelector('input,textarea');const proto=field instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(field,value);field.dispatchEvent(new Event('input',{bubbles:true}))};set('To','recipient@example.test');set('Subject','First reviewed draft');set('Message','The first revision must be replaced before approval.');return true})()`)
    await waitFor(send, `[...document.querySelectorAll('[aria-label="Gideon artifact attachments"] option')].some(option=>option.innerText.includes('Reviewed attachment'))`)
    await evaluate(send, `(()=>{const select=document.querySelector('[aria-label="Gideon artifact attachments"]');select.options[0].selected=true;select.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText==='Save draft').click()`)
    await waitFor(send, `document.body.innerText.includes('Draft saved to this account.')`)
    await evaluate(send, `[...document.querySelectorAll('[aria-label="Mailbox views"] button')].find(button=>button.innerText.startsWith('Drafts')).click()`)
    await waitFor(send, `document.querySelector('[aria-label="Outbound drafts"] button')`)
    await evaluate(send, `document.querySelector('[aria-label="Outbound drafts"] button').click()`)
    await waitFor(send, `document.querySelector('#mail-detail-title')?.innerText==='First reviewed draft'`)
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText==='Review this draft').click()`)
    await waitFor(send, `document.querySelector('[aria-label="Outbound draft review"]') && document.body.innerText.includes('First reviewed draft')`)
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText==='Approve this exact draft').click()`)
    await waitFor(send, `document.body.innerText.includes('Approved the exact content')`)
    currentPhase = 'sending once through the loopback SMTP sink and checking durable outcome after reload'
    await evaluate(send, `[...document.querySelectorAll('[aria-label="Mailbox views"] button')].find(button=>button.innerText.startsWith('Drafts')).click()`)
    await waitFor(send, `document.querySelector('[aria-label="Outbound drafts"] button')?.innerText.includes('approved')`)
    await evaluate(send, `document.querySelector('[aria-label="Outbound drafts"] button').click()`)
    await waitFor(send, `document.body.innerText.includes('Delivery: not submitted')`)
    const preservedReturnContext = await evaluate(send, `(()=>{const returnTo={destination:'chat',sessionId:'previous-conversation',selectionId:'selected-message',scrollY:384,placement:{id:'chat',query:{thread:'previous'}}};window.__navigate({...window.__route,returnTo});return JSON.stringify(window.__route.returnTo)===JSON.stringify(returnTo)})()`)
    expect(preservedReturnContext).toBe(true)
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText==='Edit draft').click()`)
    await waitFor(send, `document.querySelector('#compose-heading')?.innerText==='Edit draft'`)
    const returnContextAfterEdit = await evaluate(send, `JSON.stringify(window.__route.returnTo)`)
    expect(JSON.parse(returnContextAfterEdit as string)).toEqual({ destination: 'chat', sessionId: 'previous-conversation', selectionId: 'selected-message', scrollY: 384, placement: { id: 'chat', query: { thread: 'previous' } } })
    await evaluate(send, `(()=>{const set=(label,value)=>{const field=[...document.querySelectorAll('label')].find(node=>node.textContent.startsWith(label)).querySelector('input,textarea');const proto=field instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(field,value);field.dispatchEvent(new Event('input',{bubbles:true}))};set('Subject','Approved current draft');set('Message','Only this edited revision may be sent.');return true})()`)
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText==='Save and review').click()`)
    await waitFor(send, `document.querySelector('#mail-detail-title')?.innerText==='Approved current draft' && document.body.innerText.includes('revision 3') && document.body.innerText.includes('Reviewed attachment')`)
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText==='Review this draft').click()`)
    await waitFor(send, `document.querySelector('[aria-label="Outbound draft review"]') && document.body.innerText.includes('Only this edited revision may be sent.')`)
    const returnContextAfterReview = await evaluate(send, `JSON.stringify(window.__route.returnTo)`)
    expect(JSON.parse(returnContextAfterReview as string)).toEqual({ destination: 'chat', sessionId: 'previous-conversation', selectionId: 'selected-message', scrollY: 384, placement: { id: 'chat', query: { thread: 'previous' } } })
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText==='Approve this exact draft').click()`)
    await waitFor(send, `document.body.innerText.includes('Approved the exact content')`)
    await evaluate(send, `setTimeout(()=>[...document.querySelectorAll('button')].find(button=>button.innerText==='Send once…').click(),0);true`)
    await acceptNextJavascriptDialog(send)
    await waitFor(send, `document.body.innerText.includes('Send result: accepted') || document.body.innerText.includes('Send result: uncertain')`)
    const dispatchOutcome = await evaluate(send, `fetch('/api/capabilities/communications/outbound-email/drafts',{headers:{'X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'}}).then(response=>response.json()).then(result=>{const row=result.drafts.find(item=>item.subject==='Approved current draft');return {state:row?.state,error:row?.error}})`)
    const smtpStatus = await evaluate(send, `fetch('/api/test/smtp-status').then(response=>response.json())`)
    expect(dispatchOutcome, JSON.stringify({ dispatchOutcome, smtpStatus })).toMatchObject({ state: 'accepted' })
    expect(smtpStatus, JSON.stringify({ dispatchOutcome, smtpStatus })).toMatchObject({ reachable: true })
    await waitFor(send, `document.body.innerText.includes('Send result: accepted; delivery uncertain.')`)
    await waitFor(send, `fetch('/api/test/smtp-count').then(response=>response.json()).then(result=>result.count===1)`)
    await send('Page.reload',{ignoreCache:true})
    await waitFor(send, `document.querySelector('[aria-label="Mailbox account"]') && document.body.innerText.includes('Drafts (2)')`)
    await waitFor(send, `Array.from(document.querySelectorAll('[aria-label="Mailbox views"] button')).some(button=>button.innerText.startsWith('Drafts'))`)
    await evaluate(send, `[...document.querySelectorAll('[aria-label="Mailbox views"] button')].find(button=>button.innerText.startsWith('Drafts')).click()`)
    await waitFor(send, `document.querySelector('[aria-label="Outbound drafts"] button')?.innerText.includes('accepted')`)
    await evaluate(send, `document.querySelector('[aria-label="Outbound drafts"] button').click()`)
    await waitFor(send, `document.body.innerText.includes('Delivery: uncertain · accepted')`)
    const sinkCount = await evaluate(send, `fetch('/api/test/smtp-count').then(response=>response.json()).then(result=>result.count)`)
    expect(sinkCount).toBe(1)
    const acceptedCount = await evaluate(send, `fetch('/api/capabilities/communications/outbound-email/drafts',{headers:{'X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'}}).then(response=>response.json()).then(result=>result.drafts.filter(row=>row.state==='accepted').length)`)
    expect(acceptedCount).toBe(1)
    currentPhase = 'switching back to the read-only mailbox and checking revoked access'
    await evaluate(send, `window.__navigate({...window.__route,returnTo:undefined})`)
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText.includes('Back to messages')).click()`)
    await waitFor(send, `document.querySelector('[aria-label="Mailbox account"]') && document.querySelector('[aria-label="Outbound drafts"]')`)
    const maildirSelect = `document.querySelector('[aria-label="Mailbox account"]')`
    await evaluate(send, `(()=>{const select=${maildirSelect};Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,${JSON.stringify(maildirId)});select.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await waitFor(send, `document.querySelector('[aria-label="Mailbox account"]')?.value===${JSON.stringify(maildirId)}`)
    await evaluate(send, `[...document.querySelectorAll('[aria-label="Mailbox views"] button')].find(button=>button.innerText.startsWith('All messages')).click()`)
    await waitFor(send, `document.body.innerText.includes('Native fixture message')`)
    await evaluate(send, `document.querySelector('[aria-label="Messages"] button').click()`)
    await waitFor(send, `document.querySelector('#mail-detail-title')?.innerText==='Native fixture message'`)
    const replyUnavailable = await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText.includes('Reply unavailable'))?.disabled`)
    expect(replyUnavailable).toBe(true)

    currentPhase = 'switching accounts while a real local read-state action is pending'
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText==='Mark read in Gideon')?.click()`)
    await waitFor(send, `fetch('/api/test/read-gate').then(response=>response.json()).then(result=>result.started)`)
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText.includes('Back to messages')).click()`)
    await waitFor(send, `document.querySelector('[aria-label="Mailbox account"]')?.value===${JSON.stringify(maildirId)}`)
    await evaluate(send, `(()=>{const select=document.querySelector('[aria-label="Mailbox account"]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,${JSON.stringify(imapId)});select.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await waitFor(send, `document.querySelector('[aria-label="Mailbox account"]')?.value===${JSON.stringify(imapId)} && document.body.innerText.includes('Native reply fixture')`)
    await evaluate(send, `document.querySelector('[aria-label="Messages"] button').click()`)
    await waitFor(send, `document.querySelector('#mail-detail-title')?.innerText==='Native reply fixture'`)
    const switchedReadControl = await evaluate(send, `(()=>{const control=[...document.querySelectorAll('button')].find(button=>button.innerText==='Mark read in Gideon');return { found:!!control, disabled:control?.disabled }})()`)
    expect(switchedReadControl).toEqual({ found: true, disabled: false })
    await evaluate(send, `fetch('/api/test/read-gate/release',{method:'POST'}).then(response=>response.json())`)
    await waitFor(send, `document.querySelector('button') && !document.querySelector('button').innerText.includes('Updating read state')`)
    const switchedReadControlAfterOldAction = await evaluate(send, `(()=>{const control=[...document.querySelectorAll('button')].find(button=>button.innerText==='Mark read in Gideon');return { found:!!control, disabled:control?.disabled }})()`)
    expect(switchedReadControlAfterOldAction).toEqual({ found: true, disabled: false })

    currentPhase = 'restoring the imported mailbox and verifying access revocation clears its view'
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText.includes('Back to messages')).click()`)
    await waitFor(send, `document.querySelector('[aria-label="Mailbox account"]')?.value===${JSON.stringify(imapId)}`)
    await evaluate(send, `(()=>{const select=document.querySelector('[aria-label="Mailbox account"]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,${JSON.stringify(maildirId)});select.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await waitFor(send, `document.querySelector('[aria-label="Mailbox account"]')?.value===${JSON.stringify(maildirId)} && document.body.innerText.includes('Native fixture message')`)
    await evaluate(send, `document.querySelector('[aria-label="Messages"] button').click()`)
    await waitFor(send, `document.querySelector('#mail-detail-title')?.innerText==='Native fixture message'`)
    await evaluate(send, `window.__navigate({...window.__route,returnTo:{destination:'chat',sessionId:'previous-conversation'}})`)
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText.includes('Back to messages')).click()`)
    await waitFor(send, `document.body.innerText.includes('Returned to chat previous-conversation')`)
    await evaluate(send, `window.__navigate({destination:'apps',view:'list',placement:{id:'capabilities/communications/outbound',query:{account:${JSON.stringify(maildirId)}}}})`)
    await waitFor(send, `document.querySelector('[aria-label="Mailbox account"]') && document.body.innerText.includes('Native fixture message')`)
    const revokedAccess = await evaluate(send, `window.__testMailAccessRevocation(${JSON.stringify(maildirId)})`)
    expect(revokedAccess).toEqual({ initialCount: 1, returnedStale: false, status: 403, authRequired: true })
    await evaluate(send, `window.__navigate({destination:'chat',view:'list'})`)
    await waitFor(send, `document.body.innerText.includes('Returned to chat')`)
    await evaluate(send, `window.__navigate({destination:'apps',view:'list',placement:{id:'capabilities/communications/outbound',query:{account:${JSON.stringify(maildirId)}}}})`)
    await waitFor(send, `document.body.innerText.includes('Mailbox account unavailable') && document.body.innerText.includes('Token required')`)
    await evaluate(send, `[...document.querySelectorAll('button')].find(button=>button.innerText==='Retry accounts').click()`)
    await waitFor(send, `document.body.innerText.includes('Mailbox account unavailable') && !document.body.innerText.includes('Native fixture message') && !document.body.innerText.includes('Drafts (')`)
  }, 90000)
})
