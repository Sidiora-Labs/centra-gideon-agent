import { afterAll, afterEach, beforeAll, expect, test } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import EpigeneticRecords from './EpigeneticRecords';

let server: ChildProcess; let origin = ''; let home = ''; const realFetch = globalThis.fetch;
let ownerToken = ''
function ownerFetch(input: RequestInfo | URL, init?: RequestInit) {
  const target = input instanceof Request ? input : new URL(String(input), origin)
  const url = target instanceof Request ? target.url : String(target)
  const headers = new Headers(target instanceof Request ? target.headers : undefined)
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
  if (new URL(url).origin === origin) headers.set('Authorization', `Bearer ${ownerToken}`)
  return realFetch(target, { ...init, headers })
}
afterEach(cleanup)
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'wellbeing-epigenetic-ui-')); const root = resolve('../..');
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u','-c', `
import asyncio, json
from aiohttp import web
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.workspace.capabilities.wellbeing.epigenetic_http import register
async def main():
 app=web.Application(middlewares=[token_auth_middleware()]); register(app); runner=web.AppRunner(app); await runner.setup(); site=web.TCPSite(runner,'127.0.0.1',0); await site.start(); print(json.dumps({'port':site._server.sockets[0].getsockname()[1],'token':generate_token('wellbeing-ui-owner')}),flush=True); await asyncio.Event().wait()
asyncio.run(main())`], { cwd: root, env:{...process.env,PYTHONPATH:join(root,'runtime'),GIDEON_HOME:home,GIDEON_HOSTED:'0'} });
  origin = await new Promise<string>((resolveOrigin,reject) => {
    server.once('error',reject); server.once('exit',code=>reject(new Error(`server exited ${code}`)))
    server.stdout!.once('data',data=>{const ready=JSON.parse(String(data));ownerToken=ready.token;resolveOrigin(`http://127.0.0.1:${ready.port}`)})
  })
  const refused = await realFetch(origin + '/api/capabilities/wellbeing/exports')
  expect(refused.status).toBe(403)
  expect(ownerToken).toBeTruthy()
  globalThis.fetch = ownerFetch
});
afterAll(async () => { cleanup(); globalThis.fetch=realFetch; if(server?.exitCode===null) await new Promise<void>(done=>{server.once('exit',()=>done());server.kill('SIGTERM');}); rmSync(home,{recursive:true,force:true}); });

test('records source-authored scales, corrects history, and reloads real persisted results', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing?view=epigenetic');
  const mounted = render(<EpigeneticRecords />); await screen.findByText('No epigenetic results'); fireEvent.click(screen.getAllByRole('button',{name:'New report'})[0]);
  fireEvent.change(screen.getByLabelText('Source report ID'), {target:{value:'TruAge-2026-09'}});
  fireEvent.change(screen.getByLabelText('Observed date'), {target:{value:'2026-09-20'}});
  fireEvent.change(screen.getByLabelText('Source'), {target:{value:'Owner uploaded report'}});
  fireEvent.change(screen.getByLabelText('Biological age'), {target:{value:'38.4'}});
  fireEvent.change(screen.getByLabelText('Chronological age'), {target:{value:'41'}});
  fireEvent.change(screen.getByLabelText('Pace value'), {target:{value:'0.91'}});
  fireEvent.change(screen.getByLabelText('Organ scores JSON'), {target:{value:'{"heart":{"value":36.2,"unit":"years"},"liver":{"value":72,"scale":"percentile"},"brain":null}'}});
  fireEvent.click(screen.getByRole('button',{name:'Record report'}));
  await screen.findByText(/v1: biological 38.4 years; chronological 41 years; pace 0.91 years\/year/);
  expect(screen.getByText('Evidence: source reported')).toBeInTheDocument();
  expect(screen.getByLabelText('Source')).toBeDisabled();
  const stored = await (await ownerFetch(origin + '/api/capabilities/wellbeing/epigenetic')).json();
  expect(stored.records[0].organ_scores.brain).toBeNull();
  expect(stored.records[0].evidence_basis).toBe('source_reported');
  expect(stored.records[0].source_report_id).toBe('TruAge-2026-09');
  fireEvent.change(screen.getByLabelText('Biological age'), {target:{value:'37.9'}});
  fireEvent.change(screen.getByLabelText('Notes'), {target:{value:'Corrected transcription'}});
  fireEvent.click(screen.getByRole('button',{name:'Save correction'}));
  await screen.findByText(/v2: biological 37.9 years/);
  expect(screen.getByText(/v1: biological 38.4 years/)).toBeInTheDocument();
  mounted.unmount(); render(<EpigeneticRecords />);
  const button = await screen.findByRole('button',{name:/2026-09-20 · Owner uploaded report/ }); fireEvent.click(button);
  await waitFor(()=>expect(screen.getByLabelText('Biological age')).toHaveValue(37.9));
  expect(await screen.findByText(/v2: biological 37.9 years/)).toBeInTheDocument();
});

test('rejects invented organ-score scales before persistence', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing?view=epigenetic');
  render(<EpigeneticRecords />); await waitFor(()=>expect(screen.queryByText('Loading…')).not.toBeInTheDocument()); fireEvent.click(screen.getAllByRole('button',{name:'New report'})[0]);
  fireEvent.change(screen.getByLabelText('Source report ID'), {target:{value:'Invalid'}}); fireEvent.change(screen.getByLabelText('Observed date'), {target:{value:'2026-09-21'}}); fireEvent.change(screen.getByLabelText('Source'), {target:{value:'Report'}});
  fireEvent.change(screen.getByLabelText('Organ scores JSON'), {target:{value:'{"heart":{"value":40}}'}}); fireEvent.click(screen.getByRole('button',{name:'Record report'}));
  await screen.findByRole('alert'); expect(screen.getByRole('alert').textContent).toContain('requires exactly one authored unit or scale');
  const stored = await (await ownerFetch(origin + '/api/capabilities/wellbeing/epigenetic')).json(); expect(stored.records).toHaveLength(1);
});
