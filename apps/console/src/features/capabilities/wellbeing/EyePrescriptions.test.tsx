// @vitest-environment jsdom
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest';
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import EyePrescriptions from './EyePrescriptions';

let server: ChildProcess;
let origin = '';
let home = '';
const nativeFetch = globalThis.fetch;

let ownerToken = ''
function ownerFetch(input: RequestInfo | URL, init?: RequestInit) {
  const target = input instanceof Request ? input : new URL(String(input), origin)
  const url = target instanceof Request ? target.url : String(target)
  const headers = new Headers(target instanceof Request ? target.headers : undefined)
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value))
  if (new URL(url).origin === origin) headers.set('Authorization', `Bearer ${ownerToken}`)
  return nativeFetch(target, { ...init, headers })
}
afterEach(cleanup)
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'wellbeing-eyes-ui-'));
  const root = resolve('../..');
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, json
from aiohttp import web
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
from gideon.workspace.capabilities.wellbeing.eyes_http import register
async def main():
 app=web.Application(middlewares=[token_auth_middleware()])
 register(app)
 runner=web.AppRunner(app)
 await runner.setup()
 site=web.TCPSite(runner,'127.0.0.1',0)
 await site.start()
 print(json.dumps({'port':site._server.sockets[0].getsockname()[1],'token':generate_token('wellbeing-ui-owner')}),flush=True)
 await asyncio.Event().wait()
asyncio.run(main())
`], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: home, GIDEON_HOSTED: '0' } });
  origin = await new Promise<string>((accept, reject) => {
    server.once('error', reject);
    server.once('exit', code => reject(new Error(`eye server exited ${code}`)));
    server.stdout!.once('data', data => { const ready = JSON.parse(String(data)); ownerToken = ready.token; accept(`http://127.0.0.1:${ready.port}`) });
    server.stderr!.on('data', data => { if (String(data).includes('Traceback')) reject(new Error(String(data))); });
  });
  const refused = await nativeFetch(origin + '/api/capabilities/wellbeing/exports')
  expect(refused.status).toBe(403)
  expect(ownerToken).toBeTruthy()
  globalThis.fetch = ownerFetch
});

afterAll(async () => {
  cleanup();
  globalThis.fetch = nativeFetch;
  if (server?.exitCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM'); });
  rmSync(home, { recursive: true, force: true });
});

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

test('authors, corrects, reloads and exports exact eye prescription history through real HTTP', async () => {
  window.history.replaceState(null, '', '#/capabilities/wellbeing?view=eyes');
  const mounted = render(<EyePrescriptions />);
  await screen.findByText('No eye prescriptions'); fireEvent.click(screen.getAllByRole('button',{name:'New prescription'})[0]);
  expect(screen.getByText(/does not provide medical interpretation/)).toBeInTheDocument();
  fill('Observation date', '2026-09-24');
  fill('Source', 'Optometrist paper prescription');
  fill('Left eye Sphere (D)', '-1.25');
  fill('Left eye Cylinder (D)', '-0.5');
  fill('Left eye Axis (degrees)', '90');
  fill('Right eye Sphere (D)', '-1');
  fill('Right eye Cylinder (D)', '-0.25');
  fill('Right eye Axis (degrees)', '80');
  fill('Notes', 'Authored copy');
  expect(screen.getByLabelText('Left eye Sphere (D)')).toBeValid();
  fireEvent.click(screen.getByRole('button', { name: 'Save prescription' }));

  await screen.findByText('Correction history');
  expect(screen.getByText(/v1: Left eye -1.25 D \/ -0.5 D × 90 degrees/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /2026-09-24 · Optometrist paper prescription/ })).toBeInTheDocument();
  expect(screen.getByLabelText('Source')).toBeDisabled();

  fill('Observation date', '2026-09-25');
  fill('Left eye Cylinder (D)', '-0.75');
  fill('Left eye Axis (degrees)', '95');
  fill('Notes', 'Cylinder transcription corrected');
  fireEvent.click(screen.getByRole('button', { name: 'Save correction' }));
  await screen.findByText(/v2: Left eye -1.25 D \/ -0.75 D × 95 degrees/);
  expect(screen.getByText(/v1: Left eye -1.25 D \/ -0.5 D × 90 degrees/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /2026-09-25 · Optometrist paper prescription/ })).toBeInTheDocument();

  mounted.unmount();
  render(<EyePrescriptions />);
  const reopened = await screen.findByRole('button', { name: /2026-09-25 · Optometrist paper prescription/ });
  fireEvent.click(reopened);
  await screen.findByText(/v2: Left eye -1.25 D \/ -0.75 D × 95 degrees/);
  expect(screen.getByLabelText('Left eye Cylinder (D)')).toHaveValue(-0.75);
  expect(screen.getByLabelText('Right eye Axis (degrees)')).toHaveValue(80);

  fireEvent.click(screen.getByRole('button', { name: 'Export canonical JSON' }));
  const exported = await screen.findByLabelText('Canonical eye prescription export');
  const document = JSON.parse(exported.textContent || '{}');
  expect(document.schema).toBe('gideon.eye-prescriptions');
  expect(document.version).toBe(1);
  expect(document.prescriptions).toHaveLength(1);
  expect(document.prescriptions[0].revision).toBe(2);
  expect(document.history.map((row: { revision: number }) => row.revision)).toEqual([1, 2]);
  expect(JSON.stringify(document)).not.toMatch(/diagnosis|interpretation|recommendation/i);
});

test('invalid units and values are refused without replacing the saved record', async () => {
  cleanup();
  render(<EyePrescriptions />);
  const existing = await screen.findByRole('button', { name: /2026-09-25 · Optometrist paper prescription/ });
  fireEvent.click(existing);
  await screen.findByText('Correction history');
  fill('Left eye Sphere (D)', '41');
  expect(screen.getByLabelText('Left eye Sphere (D)')).toBeInvalid();
  fireEvent.click(screen.getByRole('button', { name: 'Save correction' }));
  const current = (await (await ownerFetch(origin + '/api/capabilities/wellbeing/eyes')).json()).prescriptions[0];
  const refused = await ownerFetch(origin + '/api/capabilities/wellbeing/eyes/' + current.id, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ revision: current.revision, request_id: crypto.randomUUID(), left: { ...current.left, sphere: 41 } }),
  });
  expect(refused.status).toBe(400);
  expect((await refused.json()).error).toContain('left sphere');
  expect(screen.getByText(/v2: Left eye -1.25 D \/ -0.75 D × 95 degrees/)).toBeInTheDocument();
  await waitFor(async () => {
    const response = await ownerFetch(`${origin}/api/capabilities/wellbeing/eyes`);
    const payload = await response.json();
    expect(payload.prescriptions[0].revision).toBe(2);
    expect(payload.prescriptions[0].left.sphere).toBe(-1.25);
  });
});
