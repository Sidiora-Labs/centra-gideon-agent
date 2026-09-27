import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
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

async function waitForJsonFile(path: string, label: string): Promise<Record<string, unknown>> {
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    try { return JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown> }
    catch { await new Promise(resolve => setTimeout(resolve, 25)) }
  }
  throw new Error(`Timed out waiting for ${label}`)
}

const nativeFixture = String.raw`
import asyncio, json, os, sys
from pathlib import Path
from aiohttp import web
from gideon.core.config import config_dir
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers import auth, capabilities_identity_goals, capabilities_identity_goal_plans
from gideon.security.auth.credentials import set_password
from gideon.workspace.capabilities.identity.goal_plans import GoalPlanStore
from gideon.workspace.capabilities.identity.goals import GoalStore

async def main(origin):
    home = Path(os.environ['GIDEON_HOME'])
    assert config_dir() == home
    (home / 'config.json').write_text(json.dumps({'auth': {'login_enabled': True}}), encoding='utf-8')
    set_password('goals-owner', 'native-goals-password')
    token_auth.use_persistent_secret(); token_auth.revoke_all_sessions()
    goals_path = home / 'capabilities/identity/goals.sqlite3'
    seeded = GoalStore(goals_path).save_goal(title='Parent objective', description='Existing parent for hierarchy', request_id='seed-parent-001', expected_revision=0)
    second = GoalStore(goals_path).save_goal(title='Second record during scope transition', description='Must not inherit the first goal view', request_id='seed-second-001', expected_revision=0)
    second_plan_path = f"/api/capabilities/identity/goal-plans/{second['id']}"
    second_plan_reads = home / 'second-plan-read-count.txt'
    @web.middleware
    async def delay_second_plan(request, handler):
        if request.path == second_plan_path:
            count = int(second_plan_reads.read_text()) if second_plan_reads.exists() else 0
            second_plan_reads.write_text(str(count + 1), encoding='utf-8')
            await asyncio.sleep(0.6)
        return await handler(request)
    @web.middleware
    async def lose_committed_checkin_reply(request, handler):
        if request.method != 'POST' or request.path != '/api/capabilities/identity/goal-plans/checkins':
            return await handler(request)
        body = await request.json()
        response = await handler(request)
        if response.status != 200: return response
        attempts_path = home / 'checkin-attempts.json'
        attempts = json.loads(attempts_path.read_text()) if attempts_path.exists() else []
        attempts.append(body); attempts_path.write_text(json.dumps(attempts))
        if len(attempts) == 1:
            request.transport.close()
        return response
    checkin_conflict_injected = False
    @web.middleware
    async def native_checkin_time_conflict(request, handler):
        nonlocal checkin_conflict_injected
        if request.method == 'POST' and request.path == '/api/capabilities/identity/goal-plans/checkins' and not checkin_conflict_injected:
            body = await request.json()
            if body.get('value') == 5:
                GoalPlanStore(goals_path).checkin(goal_id=body['goal_id'], value=6, observed_at=body['observed_at'], notes='Competing native observation', request_id='competing-checkin-' + body['request_id'])
                checkin_conflict_injected = True
        return await handler(request)
    goal_conflict_injected = False
    @web.middleware
    async def native_goal_request_conflict(request, handler):
        nonlocal goal_conflict_injected
        if request.method == 'POST' and request.path == '/api/capabilities/identity/goals/goals' and not goal_conflict_injected:
            body = await request.json()
            if body.get('title') == 'Build a neighborhood garden':
                GoalStore(goals_path).save_goal(title='Competing native request', description='A distinct write holding the submitted request ID', request_id=body['request_id'])
                goal_conflict_injected = True
        return await handler(request)
    plan_conflict_entered = home / 'late-plan-conflict-entered.json'
    plan_conflict_release = home / 'late-plan-conflict-release'
    plan_conflict_completed = home / 'late-plan-conflict-completed.json'
    @web.middleware
    async def hold_stale_plan_conflict(request, handler):
        if request.method != 'POST' or request.path != '/api/capabilities/identity/goal-plans/configure':
            return await handler(request)
        body = await request.json()
        if body.get('expected_revision') != 3 or body.get('target_value') != 99:
            return await handler(request)
        plan_conflict_entered.write_text(json.dumps({'goal_id': body['goal_id'], 'expected_revision': body['expected_revision']}), encoding='utf-8')
        while not plan_conflict_release.exists(): await asyncio.sleep(0.01)
        GoalPlanStore(goals_path).configure(goal_id=body['goal_id'], parent_id=body['parent_id'], horizon=body['horizon'], milestones=body['milestones'], links=body['links'], unit=body['unit'], target_value=88, expected_revision=body['expected_revision'], request_id='concurrent-plan-' + body['request_id'])
        response = await handler(request)
        plan_conflict_completed.write_text(json.dumps({'status': response.status}), encoding='utf-8')
        return response
    app = web.Application(middlewares=[delay_second_plan, lose_committed_checkin_reply, native_checkin_time_conflict, native_goal_request_conflict, hold_stale_plan_conflict, token_auth.token_auth_middleware(port=10000)])
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
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-target-date');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'2030-03-01');input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-target-date')?.value==='2030-03-01'", 'optional goal date rendered before save')
    await browser.evaluate("document.querySelector('.gideon-goals__form button[type=submit]').click()")
    await browser.waitFor("document.body.innerText.includes('The native service rejected this goal save.') && document.getElementById('goal-description')?.value==='Make a shared growing space' && document.getElementById('goal-target-date')?.value==='2030-03-01'", 'real native request conflict preserves rejected optional fields')
    await browser.evaluate(`(()=>{const description=document.getElementById('goal-description');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(description,'');description.dispatchEvent(new Event('input',{bubbles:true}));description.dispatchEvent(new Event('change',{bubbles:true}));const target=document.getElementById('goal-target-date');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(target,'');target.dispatchEvent(new Event('input',{bubbles:true}));target.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-description')?.value==='' && document.getElementById('goal-target-date')?.value===''", 'cleared optional goal fields remain empty after editing')
    await browser.evaluate("document.querySelector('.gideon-goals__form button[type=submit]').click()")
    await browser.waitFor("Array.from(document.querySelectorAll('.gideon-goals__goal')).some(button=>button.innerText.includes('Build a neighborhood garden'))", 'new human goal saved')
    const nativeGoal = await browser.evaluate<{ id: string; revision: number; status: string }>("fetch('/api/capabilities/identity/goals/goals').then(response=>response.json()).then(rows=>rows.find(row=>row.title==='Build a neighborhood garden'))")
    expect(nativeGoal).toMatchObject({ status: 'active', revision: 1 })
    const optionalFields = await browser.evaluate<{ description: string; target_date: string | null }>(`fetch('/api/capabilities/identity/goals/goals/${encodeURIComponent(nativeGoal.id)}').then(response=>response.json()).then(goal=>({description:goal.description,target_date:goal.target_date}))`)
    expect(optionalFields).toEqual({ description: '', target_date: null })
    await browser.evaluate(`(()=>{const key='gideon-goals-route-trace';const trace=JSON.parse(sessionStorage.getItem(key)||'[]');const record=event=>{trace.push({event,href:location.href,readyState:document.readyState,timeOrigin:performance.timeOrigin,hasParent:!!document.getElementById('goal-parent'),hasMilestone:!!document.getElementById('new-milestone'),body:(document.body?.innerText||'').slice(0,700)});sessionStorage.setItem(key,JSON.stringify(trace.slice(-30)))};let present=false;record('trace-armed');addEventListener('beforeunload',()=>record('beforeunload'));addEventListener('pageshow',()=>record('pageshow'));addEventListener('load',()=>record('load'));const observe=()=>{const next=!!document.getElementById('goal-parent')&&!!document.getElementById('new-milestone');if(next!==present){present=next;record(next?'plan-controls-visible':'plan-controls-missing')}};new MutationObserver(observe).observe(document.documentElement,{childList:true,subtree:true});setInterval(observe,50);return true})()`)
    await browser.evaluate("Array.from(document.querySelectorAll('.gideon-goals__goal')).find(button=>button.innerText.includes('Build a neighborhood garden')).click()")
    const readyExpression = `document.querySelector('#goal-parent option[value=${JSON.stringify(parentId)}]') && document.getElementById('new-milestone') && document.querySelector('.gideon-goal-plan__identity code')?.textContent?.trim()===${JSON.stringify(nativeGoal.id)} && new URLSearchParams(location.search).get('recordId')===${JSON.stringify(nativeGoal.id)}`
    await browser.waitFor(readyExpression, 'registered GoalPlan route with native parent and matching goal IDs', 10000).catch(async error => {
      const routeDiagnostic = await browser?.evaluate<Record<string, unknown>>(`(async()=>{const [planResponse,goalsResponse]=await Promise.all([fetch('/api/capabilities/identity/goal-plans/${encodeURIComponent(nativeGoal.id)}'),fetch('/api/capabilities/identity/goals/goals')]);let plan,goals;try{plan=await planResponse.json()}catch(reason){plan=String(reason)}try{goals=await goalsResponse.json()}catch(reason){goals=String(reason)}return {url:location.href,readyState:document.readyState,timeOrigin:performance.timeOrigin,planStatus:planResponse.status,plan,goalsStatus:goalsResponse.status,goals,parentId:${JSON.stringify(parentId)},goalId:${JSON.stringify(nativeGoal.id)},body:(document.body?.innerText||'').slice(0,1000),trace:sessionStorage.getItem('gideon-goals-route-trace')}})()`)
      throw new Error(`${String(error)}; route diagnostic=${JSON.stringify(routeDiagnostic)}; browser diagnostics=${JSON.stringify(browser?.diagnostics() ?? [])}`)
    })
    await browser.waitFor(`Array.from(document.querySelectorAll('#goal-parent option')).some(option=>option.value===${JSON.stringify(parentId)})`, 'parent goal option remains rendered with the selected native record')
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
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-target-value');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'12');input.dispatchEvent(new Event('input',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-target-value')?.value==='12'", 'non-null plan target rendered before save')
    await assertAction(await browser.evaluate<boolean>("(()=>{const button=Array.from(document.querySelectorAll('.gideon-goal-plan__panel button')).find(item=>item.innerText==='Save plan');if(!button||button.disabled)return false;button.click();return true})()"), 'Save plan')
    await browser.waitFor("document.body.innerText.includes('Plan saved at the current revision.')", 'hierarchy and milestone save')
    const firstPlan = await browser.evaluate<{ target_value: number | null }>(`fetch('/api/capabilities/identity/goal-plans/${encodeURIComponent(identity.id)}').then(response=>response.json()).then(value=>value.plan)`)
    expect(firstPlan.target_value).toBe(12)
    const firstPlanTimeOrigin = await browser.evaluate<number>('performance.timeOrigin')
    await browser.navigate(`${origin}/assistant/goals?v=1&view=detail&recordKind=human-goal&recordId=${encodeURIComponent(identity.id)}&placement=goals`)
    await browser.waitFor(`performance.timeOrigin!==${firstPlanTimeOrigin} && document.getElementById('goal-target-value')?.value==='12'`, 'native non-null target restored in a fresh document')
    expect(await browser.evaluate<boolean>("document.querySelector('.gideon-goal-plan__milestones input[type=checkbox]')?.checked === false")).toBe(true)
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-checkin-value');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'4');input.dispatchEvent(new Event('input',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-checkin-value').closest('form').querySelector('button[type=submit]').disabled===false", 'check-in measurement state rendered before save')
    await browser.evaluate("document.getElementById('goal-checkin-value').closest('form').querySelector('button[type=submit]').click()")
    await browser.waitFor("document.body.innerText.includes('A measurement save needs confirmation.') && document.querySelector('#goal-checkins')?.parentElement?.innerText.includes('Retry check-in')", 'committed native check-in with lost reply and persisted retry')
    const firstAttempt = JSON.parse(await readFile(join(native.home, 'checkin-attempts.json'), 'utf8')) as Array<Record<string, unknown>>
    expect(firstAttempt).toHaveLength(1)
    const beforeRetry = await browser.evaluate<{ checkins: unknown[] }>(`fetch('/api/capabilities/identity/goal-plans/${encodeURIComponent(identity.id)}').then(response=>response.json())`)
    expect(beforeRetry.checkins).toHaveLength(1)
    await browser.navigate(`${origin}/assistant/goals?v=1&view=detail&recordKind=human-goal&recordId=${encodeURIComponent(identity.id)}&placement=goals`)
    await browser.waitFor("document.querySelector('#goal-checkins')?.parentElement?.innerText.includes('Retry check-in') && document.getElementById('goal-checkin-value')?.value==='4'", 'pending check-in restored from scoped storage after reload')
    await browser.evaluate("document.getElementById('goal-checkin-value').closest('form').querySelector('button[type=submit]').click()")
    await browser.waitFor("document.body.innerText.includes('Measurement saved to this goal.')", 'idempotent retry reconciles the committed check-in')
    await browser.waitFor("document.querySelector('#goal-checkins')?.parentElement?.innerText.includes('4 units') && document.getElementById('goal-session-title').disabled===false", 'native projection refresh finishes before entering the next draft')
    const attempts = JSON.parse(await readFile(join(native.home, 'checkin-attempts.json'), 'utf8')) as Array<Record<string, unknown>>
    expect(attempts).toHaveLength(2)
    expect(attempts[1]).toEqual(attempts[0])
    const afterRetry = await browser.evaluate<{ checkins: Array<{ id: string; value: number }> }>(`fetch('/api/capabilities/identity/goal-plans/${encodeURIComponent(identity.id)}').then(response=>response.json())`)
    expect(afterRetry.checkins).toHaveLength(1)
    expect(afterRetry.checkins[0]).toMatchObject({ value: 4 })
    await browser.evaluate(`(()=>{const value=document.getElementById('goal-checkin-value');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(value,'5');value.dispatchEvent(new Event('input',{bubbles:true}));const notes=document.getElementById('goal-checkin-notes');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(notes,'Clear this rejected note');notes.dispatchEvent(new Event('input',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-checkin-value')?.value==='5' && document.getElementById('goal-checkin-notes')?.value==='Clear this rejected note'", 'check-in edit rendered before a real duplicate-time conflict')
    await browser.evaluate("document.getElementById('goal-checkin-value').closest('form').querySelector('button[type=submit]').click()")
    await browser.waitFor("document.body.innerText.includes('The measurement was rejected. Review the value and try again.') && document.getElementById('goal-checkin-notes')?.value==='Clear this rejected note'", 'native duplicate observation returns a real rejected check-in')
    await browser.evaluate(`(()=>{const notes=document.getElementById('goal-checkin-notes');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(notes,'');notes.dispatchEvent(new Event('input',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-checkin-notes')?.value===''", 'cleared check-in note remains empty after editing')
    await browser.evaluate("document.getElementById('goal-checkin-value').closest('form').querySelector('button[type=submit]').click()")
    await browser.waitFor("document.body.innerText.includes('Measurement saved to this goal.')", 'edited rejected check-in saves through the native endpoint')
    const clearedNote = await browser.evaluate<{ checkins: Array<{ value: number; notes: string }> }>(`fetch('/api/capabilities/identity/goal-plans/${encodeURIComponent(identity.id)}').then(response=>response.json())`)
    expect(clearedNote.checkins).toHaveLength(3)
    expect(clearedNote.checkins.find(item=>item.value===5)?.notes).toBe('')
    await browser.waitFor("document.querySelector('#goal-checkins')?.parentElement?.innerText.includes('5 units')", 'cleared-note check-in refresh completes before the next action')
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-session-title');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Planting bed workshop');input.dispatchEvent(new Event('input',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-session-title')?.value==='Planting bed workshop'", 'session title state rendered before start entry')
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-session-start');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'2030-01-01T10:00');input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-session-start')?.value==='2030-01-01T10:00'", 'session start rendered before end entry')
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-session-end');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'2030-01-01T11:00');input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-session-end')?.value==='2030-01-01T11:00' && document.getElementById('goal-session-start')?.value==='2030-01-01T10:00'", 'session dates rendered before save')
    await browser.evaluate("document.getElementById('goal-session-title').closest('form').querySelector('button[type=submit]').click()")
    await browser.waitFor("document.body.innerText.includes('Session saved to this goal.')", 'real native goal session')
    expect(await browser.evaluate<boolean>("document.querySelector('.gideon-goal-plan__milestones input[type=checkbox]')?.checked === false")).toBe(true)
    try {
      await browser.waitFor(`(()=>{const select=document.getElementById('goal-source-select');const option=Array.from(select?.options??[]).find(item=>item.textContent.includes('Planting bed workshop'));return !!select && !select.disabled && !!option && !option.disabled})()`, 'saved session appears as an enabled source option')
    } catch (error) {
      const sourceState = await browser.evaluate(`(()=>{const select=document.getElementById('goal-source-select');return {value:select?.value??null,disabled:select?.disabled??null,options:Array.from(select?.options??[]).map(option=>({value:option.value,text:option.textContent,disabled:option.disabled})),loading:document.body.innerText.includes('Loading the human goal and its plan…'),status:Array.from(document.querySelectorAll('[role="status"]')).map(item=>item.textContent)}})()`)
      throw new Error(`${String(error)}; source select state=${JSON.stringify(sourceState)}`)
    }
    const selectedSession = await browser.evaluate<boolean>(`(()=>{const select=document.getElementById('goal-source-select');const option=Array.from(select?.options??[]).find(item=>item.textContent.includes('Planting bed workshop'));if(!select||!option||select.disabled||option.disabled)return false;Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,option.value);select.dispatchEvent(new Event('change',{bubbles:true}));return select.value===option.value})()`)
    expect(selectedSession).toBe(true)
    await browser.waitFor("document.querySelector('#goal-source-select')?.closest('.gideon-goal-plan__add-row').querySelector('button')?.disabled===false", 'session source state rendered before linking')
    await browser.evaluate("document.querySelectorAll('.gideon-goal-plan__add-row button')[1].click()")
    await browser.waitFor("document.querySelector('#goal-sources')?.parentElement?.innerText.includes('Planting bed workshop') && document.querySelector('#goal-sources')?.parentElement?.innerText.includes('session')", 'linked source row rendered before completion')
    await browser.evaluate("document.querySelector('.gideon-goal-plan__milestones input[type=checkbox]').click()")
    await browser.waitFor("document.querySelector('.gideon-goal-plan__section-title span')?.innerText==='1 of 1 complete'", 'explicit milestone completion rendered before plan save')
    await browser.evaluate("Array.from(document.querySelectorAll('.gideon-goal-plan__panel button')).find(button=>button.innerText==='Save plan').click()")
    await browser.waitFor("document.body.innerText.includes('Plan saved at the current revision.')", 'explicit human milestone completion and source link')
    const saved = await browser.evaluate<{ queryId: string; checked: boolean; checkin: string; session: string; source: string; hierarchy: string; ratio: string; plan: { revision: number; parent_id: string; target_value: number | null; milestones: { done: boolean }[]; links: { kind: string; id: string }[] }; parentChildren: string[] }>(`(async()=>{const [plan,parent]=await Promise.all([fetch('/api/capabilities/identity/goal-plans/${encodeURIComponent(identity.id)}').then(response=>response.json()),fetch('/api/capabilities/identity/goal-plans/${encodeURIComponent(parentId)}').then(response=>response.json())]);return {queryId:new URLSearchParams(location.search).get('recordId')||'',checked:document.querySelector('.gideon-goal-plan__milestones input[type=checkbox]')?.checked??false,checkin:document.querySelector('#goal-checkins')?.parentElement?.innerText||'',session:document.querySelector('#goal-sessions')?.parentElement?.innerText||'',source:document.querySelector('#goal-sources')?.parentElement?.innerText||'',hierarchy:document.querySelector('#goal-hierarchy')?.parentElement?.innerText||'',ratio:Array.from(document.querySelectorAll('.gideon-goal-plan__panel p')).find(item=>item.innerText.includes('Explicit progress'))?.innerText||'',plan:plan.plan,parentChildren:parent.children}})()`)
    expect(saved).toMatchObject({ queryId: identity.id, checked: true })
    expect(saved.checkin).toContain('4 units')
    expect(saved.session).toContain('Planting bed workshop')
    expect(saved.source).toContain('session')
    expect(saved.hierarchy).toContain('Parent: Parent objective')
    expect(saved.parentChildren).toContain(identity.id)
    expect(saved.plan.revision).toBe(2)
    expect(saved.plan.parent_id).toBe(parentId)
    expect(saved.plan.target_value).toBe(12)
    expect(saved.plan.milestones).toContainEqual(expect.objectContaining({ done: true }))
    expect(saved.plan.links).toContainEqual(expect.objectContaining({ kind: 'session' }))
    expect(saved.ratio).toContain('100%')
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-target-value');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'');input.dispatchEvent(new Event('input',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-target-value')?.value===''", 'explicit target clearing rendered before save')
    await browser.evaluate("Array.from(document.querySelectorAll('.gideon-goal-plan__panel button')).find(button=>button.innerText==='Save plan').click()")
    await browser.waitFor("document.body.innerText.includes('Plan saved at the current revision.')", 'explicit null plan target saved')
    const clearedTarget = await browser.evaluate<{ revision: number; target_value: number | null }>(`fetch('/api/capabilities/identity/goal-plans/${encodeURIComponent(identity.id)}').then(response=>response.json()).then(value=>value.plan)`)
    expect(clearedTarget).toMatchObject({ revision: 3, target_value: null })
    const screenshotDirectory = process.env.GIDEON_EVIDENCE_DIR
    if (screenshotDirectory) {
      await mkdir(screenshotDirectory, { recursive: true })
      const capture = async (name: string) => {
        const result = await browser?.command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
        if (typeof result?.data !== 'string') throw new Error(`Chromium did not return screenshot data for ${name}`)
        await writeFile(join(screenshotDirectory, name), Buffer.from(result.data, 'base64'))
      }
      await browser.command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false })
      await capture('personal-04-goals-desktop.png')
      await browser.command('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
      await capture('personal-04-goals-narrow.png')
      await browser.command('Emulation.clearDeviceMetricsOverride')
    }
    await browser.navigate(`${origin}/assistant/goals?v=1&view=detail&recordKind=human-goal&recordId=${encodeURIComponent(identity.id)}&placement=goals`)
    await browser.waitFor("document.querySelector('.gideon-goal-plan__milestones input[type=checkbox]')?.checked === true", 'persisted explicit milestone after reload')
    expect(await browser.evaluate<string>("document.querySelector('.gideon-goal-plan__identity code')?.innerText || ''")).toBe(identity.id)
    expect(await browser.evaluate<string>("document.getElementById('goal-target-value')?.value ?? 'missing'")).toBe('')
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-target-value');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'99');input.dispatchEvent(new Event('input',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-target-value')?.value==='99'", 'old goal plan draft rendered before held save')
    await browser.evaluate("Array.from(document.querySelectorAll('.gideon-goal-plan__panel button')).find(button=>button.innerText==='Save plan').click()")
    const heldConflict = await waitForJsonFile(join(native.home, 'late-plan-conflict-entered.json'), 'held old-goal plan write')
    expect(heldConflict).toMatchObject({ goal_id: identity.id, expected_revision: 3 })
    await browser.evaluate(`(()=>{history.pushState(null,'','/assistant/goals?v=1&view=detail&recordKind=human-goal&recordId=${encodeURIComponent(secondId)}&placement=goals');dispatchEvent(new PopStateEvent('popstate'));return true})()`)
    await browser.waitFor(`new URLSearchParams(location.search).get('recordId')===${JSON.stringify(secondId)} && !document.body.innerText.includes('Build a neighborhood garden')`, 'record transition masks the previous goal synchronously')
    expect(await browser.evaluate<boolean>("!document.body.innerText.includes('Prepare the first planting beds') && document.querySelector('.gideon-goal-plan__milestones input[aria-label=\"Milestone title\"]')===null")).toBe(true)
    await browser.waitFor(`new URLSearchParams(location.search).get('recordId')===${JSON.stringify(secondId)} && document.body.innerText.includes('Loading the human goal and its plan…') && !document.body.innerText.includes('Build a neighborhood garden')`, 'prior goal remains masked while the second native plan is pending')
    await browser.waitFor(`document.querySelector('.gideon-goal-plan__header h1')?.innerText==='Second record during scope transition'`, 'new native goal content loaded after transition')
    await browser.evaluate(`(()=>{const input=document.getElementById('goal-target-value');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'77');input.dispatchEvent(new Event('input',{bubbles:true}));return true})()`)
    await browser.waitFor("document.getElementById('goal-target-value')?.value==='77'", 'new goal draft rendered while the old write is held')
    const secondPlanReadBaseline = Number(await readFile(join(native.home, 'second-plan-read-count.txt'), 'utf8'))
    expect(secondPlanReadBaseline).toBeGreaterThanOrEqual(1)
    await writeFile(join(native.home, 'late-plan-conflict-release'), 'release', 'utf8')
    const completedConflict = await waitForJsonFile(join(native.home, 'late-plan-conflict-completed.json'), 'native old-goal conflict response')
    expect(completedConflict).toEqual({ status: 409 })
    await new Promise(resolve => setTimeout(resolve, 750))
    expect(await readFile(join(native.home, 'second-plan-read-count.txt'), 'utf8')).toBe(String(secondPlanReadBaseline))
    await browser.waitFor(`new URLSearchParams(location.search).get('recordId')===${JSON.stringify(secondId)} && document.getElementById('goal-target-value')?.value==='77'`, 'new goal draft remains after the old conflict finishes')
  }, 60_000)
})
