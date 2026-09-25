import { afterAll, beforeAll, expect, test } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, join } from 'node:path'
import Genome from './Genome'
import { HashRouter } from 'react-router-dom'

let server: ChildProcess
let origin: string
let home: string
const networkFetch = globalThis.fetch
beforeAll(async () => {
  home = mkdtempSync(join(tmpdir(), 'gideon-genome-ui-'))
  const root = resolve('../..')
  server = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-u', '-c', `
import asyncio, os
from pathlib import Path
from aiohttp import web
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_genome import register
async def main():
 app = web.Application()
 register(app, Path(os.environ['GIDEON_HOME']))
 runner = web.AppRunner(app)
 await runner.setup()
 site = web.TCPSite(runner, '127.0.0.1', 0)
 await site.start()
 print(site._server.sockets[0].getsockname()[1], flush=True)
 await asyncio.Event().wait()
asyncio.run(main())
`], { cwd: root, env: { ...process.env, PYTHONPATH: join(root, 'runtime'), GIDEON_HOME: home } })
  origin = await new Promise<string>((resolveOrigin, reject) => {
    server.once('error', reject)
    server.once('exit', code => reject(new Error(`HTTP server exited ${code}`)))
    server.stdout!.once('data', data => resolveOrigin(`http://127.0.0.1:${String(data).trim()}`))
    server.stderr!.on('data', data => { if (String(data).includes('Traceback')) reject(new Error(String(data))) })
  })
  globalThis.fetch = (input, init) => networkFetch(new URL(String(input), origin), init)
})
afterAll(async () => {
  cleanup()
  globalThis.fetch = networkFetch
  if (server?.exitCode === null) await new Promise<void>(resolveExit => { server.once('exit', () => resolveExit()); server.kill('SIGTERM') })
  rmSync(home, { recursive: true, force: true })
})

const base = '/api/capabilities/wellbeing/genome'
const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })
const open = () => { window.location.hash = ''; return render(<HashRouter><Genome /></HashRouter>) }
const tsv = '\ufeffrsid\tchromosome\tposition\tgenotype\r\nrs123\t1\t100\tAG\r\nrs456\tX\t200\t--\r\n'

test('real source preview, original bytes, annotation revisions and filters', async () => {
  const view = open()
  await screen.findByText('No genome sources.')
  expect(screen.getByRole('button', { name: 'Preview genome' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Choose genome file'), { target: { files: [new File([tsv], 'sample.tsv')] } })
  await screen.findByText('Selected: sample.tsv')
  change('Declared assembly', 'GRCh37')
  change('Genome source', 'personal export')
  fireEvent.click(screen.getByRole('button', { name: 'Preview genome' }))
  await screen.findByText('2 variants; 0 existing.')
  expect(screen.getByText('rs123 · 1:100 · AG')).toBeInTheDocument()
  expect(screen.getByText('rs456 · X:200 · --')).toBeInTheDocument()
  expect(await (await networkFetch(origin + base + '/sources')).json()).toEqual({ sources: [] })
  fireEvent.click(screen.getByRole('button', { name: 'Commit genome import' }))
  await screen.findByText('Indexed 2 variants; 0 duplicates.')
  await screen.findByRole('button', { name: 'rs123: AG · 1:100' })
  const catalog = await (await networkFetch(origin + base + '/sources')).json()
  expect(catalog.sources).toHaveLength(1)
  expect(catalog.sources[0].assembly).toBe('GRCh37')
  expect(catalog.sources[0].source).toBe('personal export')
  const download = screen.getByRole('link', { name: 'Download original genome' })
  const original = await networkFetch(new URL(download.getAttribute('href')!, origin))
  expect(original.headers.get('Content-Disposition')).toContain('attachment')
  expect(Array.from(new Uint8Array(await original.arrayBuffer()))).toEqual(Array.from(new TextEncoder().encode(tsv)))
  fireEvent.click(screen.getByRole('button', { name: 'rs123: AG · 1:100' }))
  await screen.findByRole('heading', { name: 'Annotate rs123' })
  expect(screen.getByLabelText('Authored annotation')).toHaveValue('')
  change('Authored annotation', 'Personal source review')
  change('Annotation source', 'manual note')
  fireEvent.click(screen.getByRole('button', { name: 'Save annotation' }))
  await screen.findByText('Revision 2: Personal source review · manual note')
  expect(screen.getByText(/Revision 1: No authored annotation/)).toBeInTheDocument()
  change('Authored annotation', 'Revised personal note')
  fireEvent.click(screen.getByRole('button', { name: 'Save annotation' }))
  await screen.findByText('Revision 3: Revised personal note · manual note')
  expect(screen.getByText('Revision 2: Personal source review · manual note')).toBeInTheDocument()
  const rows = await (await networkFetch(origin + base + '/sources/' + catalog.sources[0].id + '/variants')).json()
  expect(rows.variants[0].revision).toBe(3)
  expect(rows.variants[0].genotype).toBe('AG')
  expect(rows.variants[0].annotation_source).toBe('manual note')
  change('Exact chromosome', 'X')
  fireEvent.click(screen.getByRole('button', { name: 'Filter variants' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'rs123: AG · 1:100' })).not.toBeInTheDocument())
  expect(screen.getByRole('button', { name: 'rs456: -- · X:200' })).toBeInTheDocument()
  change('Exact variant ID', 'not-found')
  fireEvent.click(screen.getByRole('button', { name: 'Filter variants' }))
  await screen.findByText('No matching variants.')
  view.unmount()
  render(<HashRouter><Genome /></HashRouter>)
  await screen.findByText('Revision 3: Revised personal note · manual note')
  expect(screen.getByLabelText('Authored annotation')).toHaveValue('Revised personal note')
})

test('VCF sample selection, replay and malformed files preserve sources', async () => {
  open()
  await screen.findByRole('button', { name: 'sample.tsv · GRCh37 · 2 variants' })
  const vcf = '##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tAlice\tBob\n1\t12\trsUI\tA\tG,T\t.\tPASS\t.\tGT\t1|2\t./0\n'
  fireEvent.change(screen.getByLabelText('Choose genome file'), { target: { files: [new File([vcf], 'sample.vcf')] } })
  await screen.findByText('Selected: sample.vcf')
  change('Genome format', 'vcf')
  change('Declared assembly', 'GRCh38')
  change('Genome source', 'sample source')
  fireEvent.click(screen.getByRole('button', { name: 'Preview genome' }))
  expect((await screen.findByRole('alert')).textContent).toContain('explicit VCF sample')
  change('VCF sample (required with multiple samples)', 'Alice')
  fireEvent.click(screen.getByRole('button', { name: 'Preview genome' }))
  await screen.findByText('1 variants; 0 existing.')
  expect(screen.getByText('rsUI · 1:12 · G|T')).toBeInTheDocument()
  change('Genome source', 'revised sample source')
  expect(screen.queryByRole('button', { name: 'Commit genome import' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Preview genome' }))
  await screen.findByRole('button', { name: 'Commit genome import' })
  fireEvent.click(screen.getByRole('button', { name: 'Commit genome import' }))
  await screen.findByText('Indexed 1 variants; 0 duplicates.')
  await screen.findByRole('button', { name: 'rsUI: G|T · 1:12' })
  expect(screen.getByText('revised sample source · GRCh38 · Alice')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Preview genome' }))
  await screen.findByText('1 variants; 1 existing.')
  fireEvent.click(screen.getByRole('button', { name: 'Commit genome import' }))
  await screen.findByText('Indexed 1 variants; 0 duplicates.')
  const catalog = await (await networkFetch(origin + base + '/sources')).json()
  expect(catalog.sources).toHaveLength(2)
  fireEvent.change(screen.getByLabelText('Choose genome file'), { target: { files: [new File(['invalid data'], 'broken.vcf')] } })
  await screen.findByText('Selected: broken.vcf')
  fireEvent.click(screen.getByRole('button', { name: 'Preview genome' }))
  expect((await screen.findByRole('alert')).textContent).toContain('expected genome header')
  expect(screen.queryByRole('button', { name: 'Commit genome import' })).not.toBeInTheDocument()
  expect(await (await networkFetch(origin + base + '/sources')).json()).toEqual(catalog)
})
