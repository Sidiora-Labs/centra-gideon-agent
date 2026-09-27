import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterAll, describe, expect, it } from 'vitest'
import { startBrowserHarness, startViteEntryServer, type BrowserHarness } from '../../../test-support/browserHarness'
import { startNativeServer, type NativeServer } from '../../../test-support/nativeServer'

const root = resolve(process.cwd(), '../..')
const temporary: string[] = []
let native: NativeServer | undefined
let vite: Awaited<ReturnType<typeof startViteEntryServer>> | undefined
let browser: BrowserHarness | undefined

async function availablePort(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(ready => server.listen(0, '127.0.0.1', ready))
  const address = server.address()
  const port = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(closed => server.close(() => closed()))
  return port
}

const nativeFixture = String.raw`
import asyncio, json, os, sys
from pathlib import Path
from aiohttp import web
from gideon.core.config import loader
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers import auth, capabilities_identity_goals, capabilities_identity_goal_plans
from gideon.security.auth import credentials
from gideon.workspace.capabilities.identity.goals import GoalStore

async def main(origin):
    home = Path(os.environ['GIDEON_HOME'])
    assert loader.config_dir() == home
    assert credentials.config_dir() == home
    (home / 'config.json').write_text(json.dumps({'auth': {'login_enabled': True}}), encoding='utf-8')
    credentials.set_password('goals-owner', 'native-goals-password')
    token_auth.use_persistent_secret(); token_auth.revoke_all_sessions()
    goals_path = home / 'capabilities/identity/goals.sqlite3'
    seeded = GoalStore(goals_path).save_goal(title='Parent objective', description='Existing parent for hierarchy', request_id='seed-parent-001', expected_revision=0)
    second = GoalStore(goals_path).save_goal(title='Second record during scope transition', description='Must not inherit the first goal view', request_id='seed-second-001', expected_revision=0)
    second_plan_path = f"/api/capabilities/identity/goal-plans/{second['id']}"
    @web.middleware
    async def delay_second_plan(request, handler):
        if request.path == second_plan_path: await asyncio.sleep(0.6)
        return await handler(request)
    app = web.Application(middlewares=[delay_second_plan, token_auth.token_auth_middleware(port=10000)])
    app['port'] = 10000; app['allowed_origins'] = {origin}
    app.router.add_get('/api/auth/status', auth.api_login_status); app.router.add_get('/api/auth/session', auth.api_auth_session)
    app.router.add_post('/api/auth/login', auth.api_auth_login); app.router.add_post('/api/auth/logout', auth.api_auth_logout)
    capabilities_identity_goals.register(app, store_path=goals_path)
    capabilities_identity_goal_plans.register(app, home=home)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    api_port = site._server.sockets[0].getsockname()[1]
    print(json.dumps({'api_port': api_port, 'control_port': api_port, 'parent_id': seeded['id'], 'second_id': second['id']}), flush=True)
    try: await asyncio.Event().wait()
    finally: await runner.cleanup()

asyncio.run(main(sys.argv[1]))
`

afterAll(async () => {
  await browser?.close()
  await vite?.close()
  await native?.stop()
  for (const path of temporary) await rm(path, { recursive: true, force: true })
})

describe('Compass Goals native browser journey', () => {
  it('keeps hierarchy, milestones, sessions, check-ins, and linked sources on one human goal across reload', async () => {
    const entryDirectory = await mkdtemp(join(tmpdir(), 'gideon-personal-goals-entry-')); temporary.push(entryDirectory)
    const fixture = join(entryDirectory, 'native_goals_server.py')
    await writeFile(fixture, nativeFixture, 'utf8')
    const port = await availablePort()
    const origin = `http://127.0.0.1:${port}`
    native = await startNativeServer({ script: fixture, origin, repositoryRoot: root })
    const parentId = String(native.startup.parent_id)
    const secondId = String(native.startup.second_id)
    const entryFile = join(entryDirectory, 'goals-entry.tsx')
    await writeFile(entryFile, `import React from 'react'
import { createRoot } from 'react-dom/client'
import { OwnerSignIn, ownerScope, readLoginStatus, readOwnerSession, signInOwner } from '/src/shared/auth.web.tsx'
import { createShellRoute, parseShellRoute, serializeShellRoute } from '/src/shared/shell/shellRoutes.ts'
import { personalModuleDefinitions } from '/src/features/personal/moduleDefinitions.web.ts'
function Root() {
  const [owner,setOwner] = React.useState(null), [status,setStatus] = React.useState(null), [error,setError] = React.useState('')
  const [route,setRoute] = React.useState(() => parseShellRoute(location.href,location.origin)), [View,setView] = React.useState(null)
  React.useEffect(() => { void readLoginStatus().then(setStatus); void readOwnerSession().then(setOwner).catch(() => undefined); const pop=()=>setRoute(parseShellRoute(location.href,location.origin)); addEventListener('popstate',pop); return()=>removeEventListener('popstate',pop) },[])
  React.useEffect(() => { if(!owner||route.kind!=='route'){setView(null);return} let active=true; const module=personalModuleDefinitions.find(item=>item.matches(route)); if(!module){setView(null);return()=>{active=false}} const scope=ownerScope(location.origin,owner); void module.resolve(scope,route).then(state=>state==='available'?module.load():null).then(loaded=>{if(active)setView(()=>loaded?.default??null)}); return()=>{active=false} },[owner,route])
  const navigate=next=>{history.pushState(null,'',serializeShellRoute(next));setRoute(parseShellRoute(location.href,location.origin))}
  if(!owner)return <OwnerSignIn status={status} error={error} onRetry={()=>void readOwnerSession().then(setOwner).catch(()=>undefined)} onSignIn={async(user,password,token)=>{try{await signInOwner(user,password,token);setOwner(await readOwnerSession())}catch(reason){setError(String(reason))}}}/>
  if(route.kind!=='route')return null
  if(!View)return <p role="status">Resolving Compass Goals…</p>
  return React.createElement(View,{route,scope:ownerScope(location.origin,owner),navigate,onReturn:()=>navigate(createShellRoute('goals',{view:'list',placement:{id:'goals'}}))})
}
createRoot(document.getElementById('root')).render(<Root/>)
`, 'utf8')
    vite = await startViteEntryServer({ root: join(root, 'apps/assistant'), port, entryFile, apiOrigin: native.apiOrigin })
    browser = await startBrowserHarness({ windowSize: { width: 1440, height: 1000 } })
    await browser.navigate(`${origin}/assistant/goals?v=1&view=list&placement=goals`)
    await browser.waitFor("document.getElementById('gideon-password')", 'real owner sign-in form')
    await browser.evaluate(`(()=>{const input=document.getElementById('gideon-username');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'goals-owner');input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('gideon-username')?.value==='goals-owner'", 'owner name rendered before password entry')
    await browser.evaluate(`(()=>{const input=document.getElementById('gideon-password');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'native-goals-password');input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('gideon-password')?.value==='native-goals-password'", 'password rendered before sign in')
    await browser.evaluate("document.querySelector('form').requestSubmit()")
    await browser.waitFor("document.querySelector('.gideon-goals__goal')", 'seeded human parent goal')
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-title');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Build a neighborhood garden');input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-title')?.closest('form').querySelector('button[type=submit]').disabled===false", 'goal title state rendered before description entry')
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-description');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'Make a shared growing space');input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-description')?.value==='Make a shared growing space'", 'goal description rendered before save')
    await browser.evaluate("document.querySelector('.gideon-goals__form button[type=submit]').click()")
    await browser.waitFor("Array.from(document.querySelectorAll('.gideon-goals__goal')).some(button=>button.innerText.includes('Build a neighborhood garden'))", 'new human goal saved')
    const nativeGoal = await browser.evaluate<{ id: string; revision: number; status: string }>("fetch('/api/capabilities/identity/goals/goals').then(response=>response.json()).then(rows=>rows.find(row=>row.title==='Build a neighborhood garden'))")
    expect(nativeGoal).toMatchObject({ status: 'active', revision: 1 })
    await browser.evaluate(`(()=>{const key='gideon-goals-route-trace';const trace=JSON.parse(sessionStorage.getItem(key)||'[]');const record=event=>{trace.push({event,href:location.href,readyState:document.readyState,timeOrigin:performance.timeOrigin,hasParent:!!document.getElementById('goal-parent'),hasMilestone:!!document.getElementById('new-milestone'),body:(document.body?.innerText||'').slice(0,700)});sessionStorage.setItem(key,JSON.stringify(trace.slice(-30)))};let present=false;record('trace-armed');addEventListener('beforeunload',()=>record('beforeunload'));addEventListener('pageshow',()=>record('pageshow'));addEventListener('load',()=>record('load'));const observe=()=>{const next=!!document.getElementById('goal-parent')&&!!document.getElementById('new-milestone');if(next!==present){present=next;record(next?'plan-controls-visible':'plan-controls-missing')}};new MutationObserver(observe).observe(document.documentElement,{childList:true,subtree:true});setInterval(observe,50);return true})()`)
    await browser.evaluate("Array.from(document.querySelectorAll('.gideon-goals__goal')).find(button=>button.innerText.includes('Build a neighborhood garden')).click()")
    const readyExpression = `document.querySelector('#goal-parent option[value=${JSON.stringify(parentId)}]') && document.getElementById('new-milestone') && document.querySelector('.gideon-goal-plan__identity code')?.textContent?.trim()===${JSON.stringify(nativeGoal.id)} && new URLSearchParams(location.search).get('recordId')===${JSON.stringify(nativeGoal.id)}`
    await browser.waitFor(readyExpression, 'registered GoalPlan route with native parent and matching goal IDs', 10000).catch(async error => {
      const routeDiagnostic = await browser?.evaluate<Record<string, unknown>>(`(async()=>{const [planResponse,goalsResponse]=await Promise.all([fetch('/api/capabilities/identity/goal-plans/${encodeURIComponent(nativeGoal.id)}'),fetch('/api/capabilities/identity/goals/goals')]);let plan,goals;try{plan=await planResponse.json()}catch(reason){plan=String(reason)}try{goals=await goalsResponse.json()}catch(reason){goals=String(reason)}return {url:location.href,readyState:document.readyState,timeOrigin:performance.timeOrigin,planStatus:planResponse.status,plan,goalsStatus:goalsResponse.status,goals,parentId:${JSON.stringify(parentId)},goalId:${JSON.stringify(nativeGoal.id)},body:(document.body?.innerText||'').slice(0,1000),trace:sessionStorage.getItem('gideon-goals-route-trace')}})()`)
      throw new Error(`${String(error)}; route diagnostic=${JSON.stringify(routeDiagnostic)}; browser diagnostics=${JSON.stringify(browser?.diagnostics() ?? [])}`)
    })
    const identity = await browser.evaluate<{ id: string; routeId: string; parentOptions: string[] }>("({id:document.querySelector('.gideon-goal-plan__identity code')?.innerText||'',routeId:new URLSearchParams(location.search).get('recordId')||'',parentOptions:Array.from(document.querySelectorAll('#goal-parent option')).map(option=>option.value)})")
    expect(identity.id).toBe(nativeGoal.id)
    expect(identity.routeId).toBe(nativeGoal.id)
    expect(identity.parentOptions).toContain(parentId)
    const assertAction = async (present: boolean, action: string) => {
      if (!present) {
        const diagnostic = await browser?.evaluate<Record<string, unknown>>(`(async()=>{const [planResponse,goalsResponse]=await Promise.all([fetch('/api/capabilities/identity/goal-plans/${encodeURIComponent(nativeGoal.id)}'),fetch('/api/capabilities/identity/goals/goals')]);return {url:location.href,readyState:document.readyState,timeOrigin:performance.timeOrigin,body:(document.body?.innerText||'').slice(0,1200),planStatus:planResponse.status,goalsStatus:goalsResponse.status,goalId:${JSON.stringify(nativeGoal.id)},parentId:${JSON.stringify(parentId)}}})()`)
        throw new Error(`${action} was absent; route diagnostic=${JSON.stringify(diagnostic)}; browser diagnostics=${JSON.stringify(browser?.diagnostics() ?? [])}`)
      }
    }
    await assertAction(await browser.evaluate<boolean>(`(()=>{const select=document.getElementById('goal-parent');if(!select)return false;select.value=${JSON.stringify(parentId)};select.dispatchEvent(new Event('change',{bubbles:true}));return select.value===${JSON.stringify(parentId)}})()`), 'Parent selection')
    await browser.waitFor(`document.querySelector('#goal-hierarchy')?.parentElement?.innerText.includes(${JSON.stringify(`Parent: Parent objective`)})`, 'parent selection rendered before the next action')
    await assertAction(await browser.evaluate<boolean>(`(()=>{const input=document.getElementById('new-milestone');if(!input)return false;Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Prepare the first planting beds');input.dispatchEvent(new Event('input',{bubbles:true}));return input.value==='Prepare the first planting beds'})()`), 'Milestone entry')
    await browser.waitFor("document.querySelector('.gideon-goal-plan__add-row button')?.disabled===false", 'milestone draft state rendered before Add')
    await assertAction(await browser.evaluate<boolean>("(()=>{const button=Array.from(document.querySelectorAll('.gideon-goal-plan__add-row button')).find(item=>item.innerText==='Add milestone');if(!button||button.disabled)return false;button.click();return true})()"), 'Add milestone')
    await browser.waitFor(`Array.from(document.querySelectorAll('.gideon-goal-plan__milestones input[aria-label="Milestone title"]')).some(input=>input.value==='Prepare the first planting beds')`, 'milestone appears before Save')
    await assertAction(await browser.evaluate<boolean>("(()=>{const button=Array.from(document.querySelectorAll('.gideon-goal-plan__panel button')).find(item=>item.innerText==='Save plan');if(!button||button.disabled)return false;button.click();return true})()"), 'Save plan')
    await browser.waitFor("document.body.innerText.includes('Plan saved at the current revision.')", 'hierarchy and milestone save')
    expect(await browser.evaluate<boolean>("document.querySelector('.gideon-goal-plan__milestones input[type=checkbox]')?.checked === false")).toBe(true)
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-checkin-value');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'4');input.dispatchEvent(new Event('input',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-checkin-value').closest('form').querySelector('button[type=submit]').disabled===false", 'check-in measurement state rendered before save')
    await browser.evaluate("document.getElementById('goal-checkin-value').closest('form').querySelector('button[type=submit]').click()")
    await browser.waitFor("document.body.innerText.includes('Measurement saved to this goal.')", 'real native metric check-in')
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-session-title');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Planting bed workshop');input.dispatchEvent(new Event('input',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-session-title').closest('form').querySelector('button[type=submit]').disabled===false", 'session title state rendered before start entry')
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-session-start');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'2030-01-01T10:00');input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-session-start')?.value==='2030-01-01T10:00'", 'session start rendered before end entry')
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-session-end');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'2030-01-01T11:00');input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-session-end')?.value==='2030-01-01T11:00' && document.getElementById('goal-session-start')?.value==='2030-01-01T10:00'", 'session dates rendered before save')
    await browser.evaluate("document.getElementById('goal-session-title').closest('form').querySelector('button[type=submit]').click()")
    await browser.waitFor("document.body.innerText.includes('Session saved to this goal.')", 'real native goal session')
    expect(await browser.evaluate<boolean>("document.querySelector('.gideon-goal-plan__milestones input[type=checkbox]')?.checked === false")).toBe(true)
    await browser.evaluate(`(()=>{const option=Array.from(document.querySelectorAll('#goal-source-select option')).find(item=>item.textContent.includes('Planting bed workshop'));if(!option)return false;const select=document.getElementById('goal-source-select');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,option.value);select.dispatchEvent(new Event('change',{bubbles:true}));return select.value===option.value})()`)
    await browser.waitFor("document.querySelector('#goal-source-select')?.closest('.gideon-goal-plan__add-row').querySelector('button')?.disabled===false", 'session source state rendered before linking')
    await browser.evaluate("document.querySelectorAll('.gideon-goal-plan__add-row button')[1].click()")
    await browser.waitFor("document.querySelector('#goal-sources')?.parentElement?.innerText.includes('Planting bed workshop') && document.querySelector('#goal-sources')?.parentElement?.innerText.includes('Source ID')", 'linked source row rendered before completion')
    await browser.evaluate("document.querySelector('.gideon-goal-plan__milestones input[type=checkbox]').click()")
    await browser.waitFor("document.querySelector('.gideon-goal-plan__section-title span')?.innerText==='1 of 1 complete'", 'explicit milestone completion rendered before plan save')
    await browser.evaluate("Array.from(document.querySelectorAll('.gideon-goal-plan__panel button')).find(button=>button.innerText==='Save plan').click()")
    await browser.waitFor("document.body.innerText.includes('Plan saved at the current revision.')", 'explicit human milestone completion and source link')
    const saved = await browser.evaluate<{ queryId: string; checked: boolean; checkin: string; session: string; source: string; hierarchy: string; ratio: string; plan: { revision: number; parent_id: string; milestones: { done: boolean }[]; links: { kind: string; id: string }[] }; parentChildren: string[] }>(`(async()=>{const [plan,parent]=await Promise.all([fetch('/api/capabilities/identity/goal-plans/${encodeURIComponent(identity.id)}').then(response=>response.json()),fetch('/api/capabilities/identity/goal-plans/${encodeURIComponent(parentId)}').then(response=>response.json())]);return {queryId:new URLSearchParams(location.search).get('recordId')||'',checked:document.querySelector('.gideon-goal-plan__milestones input[type=checkbox]')?.checked??false,checkin:document.querySelector('#goal-checkins')?.parentElement?.innerText||'',session:document.querySelector('#goal-sessions')?.parentElement?.innerText||'',source:document.querySelector('#goal-sources')?.parentElement?.innerText||'',hierarchy:document.querySelector('#goal-hierarchy')?.parentElement?.innerText||'',ratio:Array.from(document.querySelectorAll('.gideon-goal-plan__panel p')).find(item=>item.innerText.includes('Explicit progress'))?.innerText||'',plan:plan.plan,parentChildren:parent.children}})()`)
    expect(saved).toMatchObject({ queryId: identity.id, checked: true })
    expect(saved.checkin).toContain('4 units')
    expect(saved.session).toContain('Planting bed workshop')
    expect(saved.source).toContain('session')
    expect(saved.hierarchy).toContain('Parent: Parent objective')
    expect(saved.parentChildren).toContain(identity.id)
    expect(saved.plan.revision).toBe(2)
    expect(saved.plan.parent_id).toBe(parentId)
    expect(saved.plan.milestones).toContainEqual(expect.objectContaining({ done: true }))
    expect(saved.plan.links).toContainEqual(expect.objectContaining({ kind: 'session' }))
    expect(saved.ratio).toContain('100%')
    await browser.navigate(`${origin}/assistant/goals?v=1&view=detail&recordKind=human-goal&recordId=${encodeURIComponent(identity.id)}&placement=goals`)
    await browser.waitFor("document.querySelector('.gideon-goal-plan__milestones input[type=checkbox]')?.checked === true", 'persisted explicit milestone after reload')
    expect(await browser.evaluate<string>("document.querySelector('.gideon-goal-plan__identity code')?.innerText || ''")).toBe(identity.id)
    await browser.evaluate(`(()=>{history.pushState(null,'','/assistant/goals?v=1&view=detail&recordKind=human-goal&recordId=${encodeURIComponent(secondId)}&placement=goals');dispatchEvent(new PopStateEvent('popstate'));return true})()`)
    await browser.waitFor(`new URLSearchParams(location.search).get('recordId')===${JSON.stringify(secondId)} && !document.body.innerText.includes('Build a neighborhood garden')`, 'record transition masks the previous goal synchronously')
    expect(await browser.evaluate<boolean>("!document.body.innerText.includes('Prepare the first planting beds') && document.querySelector('.gideon-goal-plan__milestones input[aria-label=\"Milestone title\"]')===null")).toBe(true)
    await browser.waitFor(`new URLSearchParams(location.search).get('recordId')===${JSON.stringify(secondId)} && document.body.innerText.includes('Loading the human goal and its plan…') && !document.body.innerText.includes('Build a neighborhood garden')`, 'prior goal remains masked while the second native plan is pending')
    await browser.waitFor(`document.querySelector('.gideon-goal-plan__header h1')?.innerText==='Second record during scope transition'`, 'new native goal content loaded after transition')
  }, 60_000)
})
