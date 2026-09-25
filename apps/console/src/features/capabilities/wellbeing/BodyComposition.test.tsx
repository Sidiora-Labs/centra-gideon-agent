import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterAll, beforeAll, expect, test } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import BodyComposition from './BodyComposition'
import { setUILanguage, type UILanguage } from '../../../shared/i18n'

let child: ChildProcess
let origin = ''
let home = ''

beforeAll(async () => {
  await setUILanguage('en', false)
  home = mkdtempSync(join(tmpdir(), 'body-composition-ui-'))
  const root = resolve('../..')
  child = spawn('/tmp/gideon-runtime-venv/bin/python', ['-u', '-c', `
import asyncio
from aiohttp import web
from gideon.workspace.capabilities.wellbeing.body_composition_http import register
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
`], { cwd: root, env: { ...process.env, PYTHONPATH: `${root}/runtime`, GIDEON_HOME: home, GIDEON_HOSTED: '0' }, stdio: ['ignore', 'pipe', 'pipe'] })
  origin = await new Promise<string>((resolveOrigin, reject) => {
    child.once('error', reject)
    child.once('exit', code => reject(new Error(`body-composition server exited ${code}`)))
    child.stdout!.once('data', data => resolveOrigin(`http://127.0.0.1:${String(data).trim()}`))
    child.stderr!.on('data', data => { if (String(data).includes('Traceback')) reject(new Error(String(data))) })
  })
})

afterAll(async () => {
  cleanup()
  if (child?.exitCode === null) await new Promise<void>(resolveExit => { child.once('exit', () => resolveExit()); child.kill('SIGTERM') })
  rmSync(home, { recursive: true, force: true })
})

const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })

test('authors, normalizes, corrects, reloads and exports a real canonical observation', async () => {
  const mounted = render(<BodyComposition baseUrl={origin} />)
  await screen.findByText('No body composition observations')
  expect(screen.getByText(/does not provide medical advice or duplicate body weight/)).toBeInTheDocument()
  change('Observed at', '2026-09-25T08:30:00+02:00')
  change('Source', 'User-authored scale transcription')
  change('Muscle percent', '41.2')
  change('Fat percent', '18.4')
  change('Bone mass', '6.6')
  change('Bone mass unit', 'lb')
  change('Temperature', '98.6')
  change('Temperature unit', 'F')
  change('Notes', 'Morning observation')
  fireEvent.click(screen.getByRole('button', { name: 'Save observation' }))
  await screen.findByRole('heading', { name: 'Canonical record' })
  expect(screen.getByText('41.2% muscle · 18.4% fat · 6.6 lb bone · 98.6 °F')).toBeInTheDocument()
  expect(screen.getByText('2.993709642 kg bone · 37 °C · User-authored scale transcription')).toBeInTheDocument()
  expect(screen.getByText('v1: 98.6 °F · Morning observation')).toBeInTheDocument()
  expect(screen.getByLabelText('Source')).toBeDisabled()
  change('Muscle percent', '40.9')
  change('Fat percent', '18.7')
  change('Bone mass', '2994')
  change('Bone mass unit', 'g')
  change('Temperature', '310.15')
  change('Temperature unit', 'K')
  change('Notes', 'Corrected paper record')
  fireEvent.click(screen.getByRole('button', { name: 'Save correction' }))
  await screen.findByText('v2: 310.15 °K · Corrected paper record')
  expect(screen.getByText('v1: 98.6 °F · Morning observation')).toBeInTheDocument()
  expect(screen.getByText('2.994 kg bone · 37 °C · User-authored scale transcription')).toBeInTheDocument()
  const catalog = await (await fetch(`${origin}/api/capabilities/wellbeing/body-composition`)).json()
  expect(catalog.records).toHaveLength(1)
  expect(catalog.records[0].revision).toBe(2)
  expect(catalog.records[0].original_values.temperature).toEqual({ value: 310.15, unit: 'K' })
  expect(JSON.stringify(catalog)).not.toContain('weight')
  mounted.unmount()
  render(<BodyComposition baseUrl={origin} />)
  const rowButton = await screen.findByRole('button', { name: '2026-09-25T08:30:00+02:00 · User-authored scale transcription' })
  fireEvent.click(rowButton)
  await screen.findByText('v2: 310.15 °K · Corrected paper record')
  expect(screen.getByLabelText('Bone mass')).toHaveValue(2994)
  expect(screen.getByLabelText('Temperature')).toHaveValue(310.15)
  fireEvent.click(screen.getByRole('button', { name: 'Export records' }))
  await screen.findByRole('heading', { name: 'Canonical export' })
  const exportText = screen.getByRole('heading', { name: 'Canonical export' }).parentElement!.textContent!
  expect(exportText).toContain('"schema_version": 1')
  expect(exportText).toContain('Corrected paper record')
  expect(exportText).toContain('Morning observation')
  expect(exportText).not.toContain('medical advice')
})

test('invalid authored percentages retain the form and create no second record', async () => {
  cleanup()
  render(<BodyComposition baseUrl={origin} />)
  await screen.findByRole('button', { name: '2026-09-25T08:30:00+02:00 · User-authored scale transcription' })
  await screen.findByRole('heading', { name: 'Canonical record' })
  fireEvent.click(screen.getByRole('button', { name: 'New observation' }))
  change('Observed at', '2026-09-26T08:30:00Z')
  change('Source', 'Manual entry')
  change('Muscle percent', '90')
  change('Fat percent', '20')
  change('Bone mass', '3')
  change('Bone mass unit', 'kg')
  change('Temperature', '37')
  change('Temperature unit', 'C')
  change('Notes', 'Should not persist')
  fireEvent.click(screen.getByRole('button', { name: 'Save observation' }))
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toContain('cannot exceed 100')
  expect(screen.getByLabelText('Source')).toHaveValue('Manual entry')
  expect(screen.getByLabelText('Muscle percent')).toHaveValue(90)
  expect(screen.getByLabelText('Fat percent')).toHaveValue(20)
  const catalog = await (await fetch(`${origin}/api/capabilities/wellbeing/body-composition`)).json()
  expect(catalog.records).toHaveLength(1)
  expect(catalog.records[0].notes).toBe('Corrected paper record')
  await waitFor(() => expect(screen.queryByText('Loading…')).not.toBeInTheDocument())
})

test('five languages retain explicit unit controls and RTL direction', async () => {
  cleanup()
  render(<BodyComposition baseUrl={origin} />)
  await screen.findByRole('button', { name: '2026-09-25T08:30:00+02:00 · User-authored scale transcription' })
  const languages: [UILanguage, string, string, string][] = [
    ['es', 'Observaciones de composición corporal', 'Unidad de masa ósea', 'Unidad de temperatura'],
    ['ar', 'ملاحظات تكوين الجسم', 'وحدة كتلة العظام', 'وحدة الحرارة'],
    ['hi', 'शारीरिक संरचना अवलोकन', 'अस्थि द्रव्यमान इकाई', 'तापमान इकाई'],
    ['zh-CN', '身体成分观察', '骨量单位', '温度单位'],
    ['en', 'Body composition observations', 'Bone mass unit', 'Temperature unit'],
  ]
  try {
    for (const [language, heading, boneLabel, temperatureLabel] of languages) {
      await act(async () => { await setUILanguage(language, false) })
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
      expect(screen.getByLabelText(boneLabel)).toHaveValue('kg')
      expect(screen.getByLabelText(temperatureLabel)).toHaveValue('C')
      expect(document.documentElement.dir).toBe(language === 'ar' ? 'rtl' : 'ltr')
    }
  } finally {
    await act(async () => { await setUILanguage('en', false) })
  }
})
