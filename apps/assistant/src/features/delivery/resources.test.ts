import { afterAll, describe, expect, it } from 'vitest'
import { startBrowserHarness } from '../../../test-support/browserHarness'
import { startNativeServer } from '../../../test-support/nativeServer'
import { startViteEntryServer } from '../../../test-support/browserHarness'
import { join, resolve } from 'node:path'
import { createServer } from 'node:net'
import type { ViteDevServer } from 'vite'
import type { BrowserHarness } from '../../../test-support/browserHarness'
import type { NativeServer } from '../../../test-support/nativeServer'

const root = resolve(process.cwd(), '../..')
let native: NativeServer | undefined
let vite: ViteDevServer | undefined
let browser: BrowserHarness | undefined

afterAll(async () => {
  await browser?.close()
  await vite?.close()
  await native?.stop()
})

async function availablePort(): Promise<number> {
  const server = createServer()
  await new Promise<void>(resolveListening => server.listen(0, '127.0.0.1', resolveListening))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(resolveClosed => server.close(() => resolveClosed()))
  return port
}

async function signIn(): Promise<void> {
  const owner = await browser!.evaluate(`(async()=>{
    const headers={'Content-Type':'application/json','X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'};
    const login=await fetch('/api/auth/login',{method:'POST',credentials:'same-origin',headers,body:JSON.stringify({username:'resource-owner',password:'correct-horse-battery-staple'})});
    if(!login.ok) throw Error('Native owner sign-in failed: '+login.status+' '+await login.text());
    const session=await fetch('/api/auth/session',{credentials:'same-origin',headers});
    if(!session.ok) throw Error('Native owner session failed: '+session.status);
    return (await session.json()).user;
  })()`)
  expect(owner).toBe('resource-owner')
}

describe('owner-scoped resource delivery', () => {
  it('opens a native PDF in the same-origin session, reconnects its authenticated stream, and denies after sign-out', async () => {
    const port = await availablePort()
    const origin = `http://127.0.0.1:${port}`
    native = await startNativeServer({
      script: join(root, 'apps/assistant/test-support/resources_server.py'), origin, repositoryRoot: root,
    })
    vite = await startViteEntryServer({
      root: join(root, 'apps/assistant'), port, entryFile: join(root, 'apps/assistant/src/features/delivery/resources.web.ts'),
      apiOrigin: native.apiOrigin,
    })
    browser = await startBrowserHarness()
    await browser.command('Page.enable')
    await browser.command('Runtime.enable')
    await browser.navigate(`${origin}/assistant/chat?v=1`)
    await browser.waitFor("location.pathname === '/assistant/chat'", 'direct assistant link')
    await signIn()
    await browser.command('Page.reload', { ignoreCache: true })
    await browser.waitFor("performance.getEntriesByType('navigation')[0]?.type === 'reload'", 'resource route reload')

    const result = await browser.evaluate<{
      artifact: { slug: string; version: number }
      mime: string
      bytes: number[]
      websocket: string
      contextualWebsocket: string
      ownerEvent: string
      retryHref: string
      download: { body: string; disposition: string; status: number }
      deniedStatus: number
      deniedAuthRequired: string
    }>(`(async()=>{
      const delivery=await import('/src/features/delivery/resources.web.ts');
      const headers={'Content-Type':'application/json','X-Gideon-API-Version':'1','X-Session-Key':'dashboard:ui'};
      const detail=await fetch('/api/artifacts/delivery-owned-pdf',{credentials:'same-origin',headers});
      if(!detail.ok) throw Error('Native PDF fixture detail failed: '+detail.status+' '+await detail.text());
      const artifact=await detail.json();
      const raw=await delivery.openOwnedResource(delivery.artifactHref(artifact.slug,true));
      const bytes=Array.from(new Uint8Array(await raw.arrayBuffer()));
      const downloaded=await delivery.openOwnedResource(delivery.outboxDownloadHref('owned-download.txt'));
      const download={status:downloaded.status,disposition:downloaded.headers.get('Content-Disposition')||'',body:await downloaded.text()};
      const socket=new WebSocket(delivery.ownedWebSocketUrl());
      const ownerEvent=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Cookie-authenticated stream did not send owner events')),5000);socket.onmessage=event=>{clearTimeout(timer);resolve(event.data)};socket.onerror=()=>{clearTimeout(timer);reject(Error('Cookie-authenticated stream failed'))}});
      socket.close();
      const second=new WebSocket(delivery.ownedWebSocketUrl());
      await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Stream reconnect timed out')),5000);second.onmessage=()=>{clearTimeout(timer);resolve(true)};second.onerror=()=>{clearTimeout(timer);reject(Error('Stream reconnect failed'))}});
      second.close();
      const contextualWebsocket=delivery.ownedWebSocketUrl('/api/ws?session=session-7&record=record-3&view=detail');
      const href=delivery.outboxDownloadHref('sample report.pdf');
      await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin',headers});
      const denied=await fetch(delivery.artifactHref(artifact.slug,true),{credentials:'same-origin'});
      const deniedStatus=denied.status;
      const deniedAuthRequired=denied.headers.get('X-Auth-Required')||'';
      return {artifact,mime:raw.headers.get('Content-Type')||'',bytes,websocket:delivery.ownedWebSocketUrl(),contextualWebsocket,ownerEvent:String(ownerEvent),retryHref:href,download,deniedStatus,deniedAuthRequired};
    })()`)
    expect(result.artifact.slug).toBe('delivery-owned-pdf')
    expect(result.mime).toContain('application/pdf')
    expect(result.bytes.slice(0, 8)).toEqual([37, 80, 68, 70, 45, 49, 46, 52])
    expect(result.websocket).toBe('ws://' + `127.0.0.1:${port}` + '/api/ws')
    expect(result.contextualWebsocket).toBe('ws://' + `127.0.0.1:${port}` + '/api/ws?session=session-7&record=record-3&view=detail')
    expect(JSON.parse(result.ownerEvent).type).toBe('sessions')
    expect(result.retryHref).toBe('/api/outbox/sample%20report.pdf')
    expect(result.download.status).toBe(200)
    expect(result.download.disposition).toMatch(/attachment/i)
    expect(result.download.body).toContain('Native owner-scoped download fixture')
    expect(result.deniedStatus).toBe(403)
    expect(result.deniedAuthRequired).toBe('true')
  }, 60000)

  it('rejects foreign, malformed, and credential-bearing resource addresses', async () => {
    const resources = await import('./resources.web')
    expect(resources.artifactHref('trusted/slash')).toBe('/api/artifacts/trusted%2Fslash')
    expect(resources.outboxDownloadHref('notes.pdf')).toBe('/api/outbox/notes.pdf')
    expect(resources.ownedResourceHref('/api/artifacts/item?session=session-7&record=record-3&view=detail&name=quarterly%20report')).toBe(
      '/api/artifacts/item?session=session-7&record=record-3&view=detail&name=quarterly%20report',
    )
    for (const key of ['token', 'access_token', 'CLIENT-SECRET', 'authorization', 'password', 'credential', 'code', 'state']) {
      expect(() => resources.ownedResourceHref(`/api/artifacts/item?${encodeURIComponent(key)}=private`)).toThrow(TypeError)
    }
    for (const filename of ['../secret', 'folder/file', 'folder\\file', '']) {
      expect(() => resources.outboxDownloadHref(filename)).toThrow(TypeError)
    }
    for (const path of ['https://example.test/api/outbox/a', '//example.test/api/outbox/a', '/api/../../secret', '/api/x#token']) {
      expect(() => resources.ownedResourceHref(path)).toThrow(TypeError)
    }
    for (const key of ['token', 'access_token', 'client_secret', 'authorization', 'password', 'credential', 'code', 'state']) {
      expect(() => resources.ownedWebSocketUrl(`/api/ws?${key}=private`)).toThrow(TypeError)
    }
  })
})
