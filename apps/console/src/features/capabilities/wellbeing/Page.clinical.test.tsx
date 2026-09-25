// @vitest-environment jsdom
import { afterAll, beforeAll, expect, test } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { setUILanguage, type UILanguage } from '../../../shared/i18n';
import Page from './Page';

let server: ChildProcess;
let origin = '';
let home = '';
const nativeFetch = globalThis.fetch;

beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'wellbeing-clinical-page-'));
  const root = resolve('../..');
  server = spawn('/tmp/gideon-runtime-venv/bin/python', ['-u', '-c', `
import asyncio
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing import register
async def main():
 app=web.Application()
 register(app)
 runner=web.AppRunner(app)
 await runner.setup()
 site=web.TCPSite(runner,'127.0.0.1',0)
 await site.start()
 print(site._server.sockets[0].getsockname()[1],flush=True)
 await asyncio.Event().wait()
asyncio.run(main())
`], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: home, GIDEON_HOSTED: '0' } });
  origin = await new Promise<string>((accept, reject) => {
    server.once('error', reject);
    server.once('exit', code => reject(new Error(`clinical page server exited ${code}`)));
    server.stdout!.once('data', data => accept(`http://127.0.0.1:${String(data).trim()}`));
    server.stderr!.on('data', data => { if (String(data).includes('Traceback')) reject(new Error(String(data))); });
  });
  globalThis.fetch = (input, init) => nativeFetch(new URL(String(input), origin), init);
}, 20000);

afterAll(async () => {
  cleanup();
  await act(async () => setUILanguage('en', false));
  globalThis.fetch = nativeFetch;
  if (server?.exitCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM'); });
  rmSync(home, { recursive: true, force: true });
});

test('shared page navigates and deep-links all clinical forms with localized wrapping navigation', async () => {
  await act(async () => setUILanguage('en', false));
  async function create(path: string, payload: object) {
    const response = await nativeFetch(origin + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    expect(response.status).toBe(201);
    return response.json() as Promise<{ id: string }>;
  }
  const seeded = {
    epigenetic: await create('/api/capabilities/wellbeing/epigenetic', { request_id: 'page-epi', source_report_id: 'Page-Epi', observed_at: '2026-09-20', source: 'Page report', biological_age: { value: 38, unit: 'years' }, chronological_age: { value: 41, unit: 'years' }, pace_of_aging: null, organ_scores: {}, notes: '' }),
    eyes: await create('/api/capabilities/wellbeing/eyes', { request_id: 'page-eyes', observed_date: '2026-09-24', source: 'Page optometrist', notes: '', left: { sphere: -1.25, sphere_unit: 'D', cylinder: -0.5, cylinder_unit: 'D', axis: 90, axis_unit: 'degrees' }, right: { sphere: -1, sphere_unit: 'D', cylinder: -0.25, cylinder_unit: 'D', axis: 80, axis_unit: 'degrees' } }),
    lifestyle: await create('/api/capabilities/wellbeing/lifestyle-profiles', { request_id: 'page-lifestyle', observed_at: '2026-09-25T08:30:00Z', source: 'Page intake', reported_sex: 'female', sex_source: 'Owner report', smoking_status: 'never', diet_quality: { value: 8, scale: { minimum: 0, maximum: 10, label: '0–10 intake' } }, stress: { value: 2, scale: { minimum: 0, maximum: 10, label: '0–10 intake' } }, reported_bmi: 24.2, condition_labels: [], reported_daily_alcohol: null }),
    'body-composition': await create('/api/capabilities/wellbeing/body-composition', { request_id: 'page-body', observed_at: '2026-09-25T08:30:00+02:00', source: 'Page scale', values: { muscle_percent: 41.2, fat_percent: 18.4, bone_mass: { value: 3, unit: 'kg' }, temperature: { value: 37, unit: 'C' } }, notes: '' }),
  };
  window.history.replaceState(null, '', '#/capabilities/wellbeing');
  let mounted = render(<Page />);
  const navigation = screen.getByRole('navigation', { name: 'Wellbeing sections' });
  expect(navigation).toHaveStyle({ display: 'flex', flexWrap: 'wrap' });
  mounted.unmount();

  const journeys = [
    ['Epigenetic results', 'epigenetic', 'Epigenetic results', '2026-09-20 · Page-Epi', 'Report history'],
    ['Eye prescriptions', 'eyes', 'Eye prescriptions', '2026-09-24 · Page optometrist · v1', 'Correction history'],
    ['Lifestyle profile', 'lifestyle', 'Lifestyle profile observations', '2026-09-25T08:30:00Z · Page intake · BMI 24.2', 'Observation history'],
    ['Body composition', 'body-composition', 'Body composition observations', '2026-09-25T08:30:00+02:00 · Page scale', 'History'],
  ] as const;
  for (const [label, view, heading, rowLabel, historyLabel] of journeys) {
    window.history.replaceState(null, '', '#/capabilities/wellbeing');
    mounted = render(<Page />);
    fireEvent.click(screen.getByRole('button', { name: label }));
    await screen.findByRole('heading', { level: 1, name: heading });
    await waitFor(() => expect(location.hash).toBe(`#/capabilities/wellbeing?view=${view}`));
    expect(screen.getByRole('button', { name: label })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(await screen.findByRole('button', { name: rowLabel }));
    await screen.findByRole('heading', { name: historyLabel });
    await waitFor(() => expect(location.hash).toBe(`#/capabilities/wellbeing?view=${view}&id=${seeded[view].id}`));
    mounted.unmount();
    mounted = render(<Page />);
    await screen.findByRole('heading', { name: historyLabel });
    expect(location.hash).toBe(`#/capabilities/wellbeing?view=${view}&id=${seeded[view].id}`);
    expect(screen.getByRole('button', { name: label })).toHaveAttribute('aria-pressed', 'true');
    mounted.unmount();
  }

  window.history.replaceState(null, '', '#/capabilities/wellbeing');
  render(<Page />);
  const localized: Array<[UILanguage, string, string]> = [
    ['es', 'Secciones de bienestar', 'Recetas oculares'],
    ['ar', 'أقسام العافية', 'وصفات العيون'],
    ['hi', 'स्वास्थ्य अनुभाग', 'नेत्र पर्चे'],
    ['zh-CN', '健康栏目', '眼镜处方'],
    ['en', 'Wellbeing sections', 'Eye prescriptions'],
  ];
  for (const [language, navLabel, eyeLabel] of localized) {
    await act(async () => setUILanguage(language, false));
    expect(screen.getByRole('navigation', { name: navLabel })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: eyeLabel })).toBeInTheDocument();
  }
}, 20000);
