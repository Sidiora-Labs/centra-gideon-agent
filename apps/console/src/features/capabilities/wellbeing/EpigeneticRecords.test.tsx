import { afterAll, beforeAll, expect, test } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import EpigeneticRecords from './EpigeneticRecords';

let server: ChildProcess; let origin = ''; let home = ''; const realFetch = globalThis.fetch;
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'wellbeing-epigenetic-ui-')); const root = resolve('../..');
  server = spawn('/tmp/gideon-runtime-venv/bin/python', ['-u','-c', `
import asyncio
from aiohttp import web
from gideon.workspace.capabilities.wellbeing.epigenetic_http import register
async def main():
 app=web.Application(); register(app); runner=web.AppRunner(app); await runner.setup(); site=web.TCPSite(runner,'127.0.0.1',0); await site.start(); print(site._server.sockets[0].getsockname()[1],flush=True); await asyncio.Event().wait()
asyncio.run(main())`], { cwd: root, env:{...process.env,PYTHONPATH:join(root,'runtime'),GIDEON_HOME:home,GIDEON_HOSTED:'0'} });
  origin = await new Promise<string>((resolveOrigin,reject) => { server.once('error',reject); server.once('exit',code=>reject(new Error(`server exited ${code}`))); server.stdout!.once('data',data=>resolveOrigin(`http://127.0.0.1:${String(data).trim()}`)); });
  globalThis.fetch = (input, init) => realFetch(new URL(String(input), origin), init);
});
afterAll(async () => { cleanup(); globalThis.fetch=realFetch; if(server?.exitCode===null) await new Promise<void>(done=>{server.once('exit',()=>done());server.kill('SIGTERM');}); rmSync(home,{recursive:true,force:true}); });

test('records source-authored scales, corrects history, and reloads real persisted results', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing?view=epigenetic');
  const mounted = render(<EpigeneticRecords />); await screen.findByText('No epigenetic results');
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
  const stored = await (await realFetch(origin + '/api/capabilities/wellbeing/epigenetic')).json();
  expect(stored.records[0].organ_scores.brain).toBeNull();
  expect(stored.records[0].evidence_basis).toBe('source_reported');
  fireEvent.change(screen.getByLabelText('Biological age'), {target:{value:'37.9'}});
  fireEvent.change(screen.getByLabelText('Notes'), {target:{value:'Corrected transcription'}});
  fireEvent.click(screen.getByRole('button',{name:'Save correction'}));
  await screen.findByText(/v2: biological 37.9 years/);
  expect(screen.getByText(/v1: biological 38.4 years/)).toBeInTheDocument();
  mounted.unmount(); render(<EpigeneticRecords />);
  const button = await screen.findByRole('button',{name:'2026-09-20 · TruAge-2026-09'}); fireEvent.click(button);
  await waitFor(()=>expect(screen.getByLabelText('Biological age')).toHaveValue(37.9));
  expect(await screen.findByText(/v2: biological 37.9 years/)).toBeInTheDocument();
});

test('rejects invented organ-score scales before persistence', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing?view=epigenetic');
  render(<EpigeneticRecords />); await waitFor(()=>expect(screen.queryByText('Loading…')).not.toBeInTheDocument()); fireEvent.click(screen.getByRole('button',{name:'New report'}));
  fireEvent.change(screen.getByLabelText('Source report ID'), {target:{value:'Invalid'}}); fireEvent.change(screen.getByLabelText('Observed date'), {target:{value:'2026-09-21'}}); fireEvent.change(screen.getByLabelText('Source'), {target:{value:'Report'}});
  fireEvent.change(screen.getByLabelText('Organ scores JSON'), {target:{value:'{"heart":{"value":40}}'}}); fireEvent.click(screen.getByRole('button',{name:'Record report'}));
  await screen.findByRole('alert'); expect(screen.getByRole('alert').textContent).toContain('requires exactly one authored unit or scale');
  const stored = await (await realFetch(origin + '/api/capabilities/wellbeing/epigenetic')).json(); expect(stored.records).toHaveLength(1);
});
