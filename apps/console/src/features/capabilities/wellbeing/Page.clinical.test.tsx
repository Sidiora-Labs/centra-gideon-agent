// @vitest-environment jsdom
import { afterAll, beforeAll, expect, test } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import Page from './Page';

let server: ChildProcess;
let origin = '';
let home = '';
const nativeFetch = globalThis.fetch;

beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'wellbeing-clinical-page-'));
  const root = resolve('../..');
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, json
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing import register
from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware
async def main():
 app=web.Application(middlewares=[token_auth_middleware()])
 register(app)
 runner=web.AppRunner(app)
 await runner.setup()
 site=web.TCPSite(runner,'127.0.0.1',0)
 await site.start()
 print(json.dumps({"port":site._server.sockets[0].getsockname()[1],"token":generate_token("clinical-page-owner")}),flush=True)
 await asyncio.Event().wait()
asyncio.run(main())
`], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: home, GIDEON_HOSTED: '0' } });
  origin = await new Promise<string>((accept, reject) => {
    server.once('error', reject);
    server.once('exit', code => reject(new Error(`clinical page server exited ${code}`)));
    let output = '';
    server.stdout!.on('data', data => {
      output += String(data);
      if (!output.includes('\n')) return;
      try {
        const ready: { port: number; token: string } = JSON.parse(output.trim());
        if (!Number.isInteger(ready.port) || !ready.token) throw new Error('Invalid clinical readiness');
        const base = `http://127.0.0.1:${ready.port}`;
        globalThis.fetch = (input, init) => {
          const url = new URL(input instanceof Request ? input.url : String(input), base);
          const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
          if (url.origin === base) headers.set('Authorization', `Bearer ${ready.token}`);
          return nativeFetch(url, { ...init, headers });
        };
        accept(base);
      } catch (error) { reject(error); }
    });
    server.stderr!.on('data', data => { if (String(data).includes('Traceback')) reject(new Error(String(data))); });
  });

}, 60000);

afterAll(async () => {
  cleanup();
  document.documentElement.lang = 'en';
  document.documentElement.dir = 'ltr';
  globalThis.fetch = nativeFetch;
  if (server?.exitCode === null) await new Promise<void>(done => { server.once('exit', () => done()); server.kill('SIGTERM'); });
  rmSync(home, { recursive: true, force: true });
});

test('shared page navigates and deep-links all clinical forms with localized section navigation', async () => {
  document.documentElement.lang = 'en';
  document.documentElement.dir = 'ltr';
  async function create(path: string, payload: object) {
    const response = await fetch(origin + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
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
  expect(navigation).toHaveClass('capability-area-navigation');
  expect(screen.getByRole('button', { name: 'Body composition' })).toBeInTheDocument();
  mounted.unmount();

  const journeys = [
    ['Epigenetic results', 'epigenetic', 'Epigenetic results', /^2026-09-20 · Page report\s*38 years$/, 'Report history'],
    ['Eye prescriptions', 'eyes', 'Eye prescriptions', /^2026-09-24 · Page optometrist\s*-1\.25 \/ -1 D$/, 'Correction history'],
    ['Lifestyle profile', 'lifestyle', 'Lifestyle profile observations', '2026-09-25T08:30:00Z · Page intake · BMI 24.2', 'Observation history'],
    ['Body composition', 'body-composition', 'Body composition observations', `${new Date('2026-09-25T08:30:00+02:00').toLocaleDateString()} · Page scale · 41.2% muscle · 18.4% fat`, 'History'],
  ] as const;
  for (const [label, view, heading, rowLabel, historyLabel] of journeys) {
    window.history.replaceState(null, '', '#/capabilities/wellbeing');
    mounted = render(<Page />);
    fireEvent.click(screen.getByRole('button', { name: label }));
    await screen.findByRole('heading', { level: 1, name: label });
    await screen.findByRole('heading', { level: 2, name: heading });
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
  mounted = render(<Page />);
  const localized = [
    ['es', 'Secciones de bienestar', 'Recetas oculares'],
    ['ar', 'أقسام العافية', 'وصفات العيون'],
    ['hi', 'स्वास्थ्य अनुभाग', 'नेत्र पर्चे'],
    ['zh-CN', '健康栏目', '眼镜处方'],
    ['en', 'Wellbeing sections', 'Eye prescriptions'],
  ] as const;
  for (const [language, navLabel, eyeLabel] of localized) {
    document.documentElement.lang = language;
    document.documentElement.dir = language === 'ar' ? 'rtl' : 'ltr';
    mounted.rerender(<Page />);
    expect(screen.getByRole('navigation', { name: navLabel })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: eyeLabel })).toBeInTheDocument();
  }
}, 20000);

test('organizations route reaches real subject, consent and organization controls without losing the record', async () => {
  document.documentElement.lang = 'en';
  document.documentElement.dir = 'ltr';
  window.history.replaceState(null, '', '#/capabilities/wellbeing');
  const mounted = render(<Page />);
  fireEvent.click(screen.getByRole('button', { name: 'Organizations' }));
  await screen.findByRole('heading', { name: 'Private identity facts' });
  expect(location.hash).toBe('#/capabilities/wellbeing?view=organizations');
  fireEvent.change(screen.getByLabelText('Subject alias'), { target: { value: 'Route owner' } });
  fireEvent.change(screen.getByLabelText('Subject source'), { target: { value: 'Owner entry' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create privacy subject' }));
  await screen.findByRole('heading', { name: 'Organizations and changed facts' });
  fireEvent.change(screen.getByLabelText('Consent method'), { target: { value: 'Owner review' } });
  fireEvent.click(screen.getByRole('button', { name: 'Record consent decision' }));
  await screen.findByText(/vault revision 1: granted/);
  fireEvent.change(screen.getByLabelText('Organization name'), { target: { value: 'Route organization' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save organization' }));
  await screen.findByRole('button', { name: 'Route organization · revision 1' });
  fireEvent.click(screen.getByRole('button', { name: 'Privacy' }));
  await screen.findByRole('button', { name: 'Route owner' });
  fireEvent.click(screen.getByRole('button', { name: 'Route owner' }));
  await screen.findByRole('button', { name: 'Route organization · revision 1' });
  fireEvent.click(screen.getByRole('button', { name: 'Organizations' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Route owner' }));
  expect(await screen.findByRole('button', { name: 'Route organization · revision 1' })).toBeInTheDocument();
  mounted.unmount();
}, 20000);
