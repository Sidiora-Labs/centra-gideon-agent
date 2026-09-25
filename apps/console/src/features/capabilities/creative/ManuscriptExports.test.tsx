import { spawn,type ChildProcess } from 'node:child_process'
import { execFile } from 'node:child_process'
import { mkdtemp,rm,writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join,resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { promisify } from 'node:util'
import { afterAll,beforeAll,expect,test } from 'vitest'
import { fireEvent,render,screen,waitFor,within } from '@testing-library/react'
import ManuscriptExports from './ManuscriptExports'

let server:ChildProcess,origin='',home='',workId='',revision=0
const run=promisify(execFile)
beforeAll(async()=>{
  home=await mkdtemp(join(tmpdir(),'gideon-export-ui-'));const root=resolve(process.cwd(),'../..');let diagnostics=''
  server=spawn(process.env.GIDEON_TEST_PYTHON||'/tmp/gideon-runtime-venv/bin/python',['checks/runtime/capabilities/creative/export_ui_server.py'],{cwd:root,env:{...process.env,PYTHONPATH:join(root,'runtime'),GIDEON_HOME:home},stdio:['ignore','pipe','pipe']})
  server.stderr!.on('data',chunk=>{diagnostics+=chunk.toString()})
  const ready=await new Promise<{port:number;work_id:string;revision:number}>((accept,reject)=>{const lines=createInterface({input:server.stdout!});lines.on('line',line=>{try{const value=JSON.parse(line);accept(value);lines.close()}catch{}});server.once('error',reject);server.once('exit',code=>reject(new Error(`server exited ${code}: ${diagnostics}`)))})
  origin=`http://127.0.0.1:${ready.port}`;workId=ready.work_id;revision=ready.revision
})
afterAll(async()=>{if(server?.exitCode===null)await new Promise<void>(done=>{server.once('exit',()=>done());server.kill('SIGTERM')});await rm(home,{recursive:true,force:true})})
const change=(name:string,value:string)=>fireEvent.change(screen.getByLabelText(name),{target:{value}})

test('renders a pinned work through real HTTP and exposes readable EPUB and PDF downloads',async()=>{
  render(<ManuscriptExports baseUrl={origin}/>)
  expect(screen.getByRole('region',{name:'Manuscript exports'})).toHaveTextContent('pin the selected work or ordered series revision')
  expect(screen.getByRole('button',{name:'Create EPUB and print PDF'})).toBeDisabled()
  change('Export source ID',workId);change('Export source revision',String(revision));change('Export title','UI Published Book');change('Export creator','UI Author');change('Export language','en-GB');change('Export identifier','urn:uuid:ui-book')
  fireEvent.click(screen.getByRole('button',{name:'Create EPUB and print PDF'}))
  const article=await screen.findByRole('article',{name:'Manuscript export UI Published Book'})
  expect(article).toHaveTextContent(`work ${workId} revision ${revision} · 1 manuscript sections`)
  expect(article).toHaveTextContent('UI Manuscript')
  expect(article).toHaveTextContent(`work revision ${revision}`)
  expect(article).toHaveTextContent('artifact creative-draft-')
  expect(article).toHaveTextContent('version 1 · SHA-256')
  const epub=within(article).getByRole('link',{name:/Download EPUB/}) as HTMLAnchorElement
  const print=within(article).getByRole('link',{name:/Download print PDF/}) as HTMLAnchorElement
  expect(epub.href).toMatch(new RegExp(`^${origin}/api/capabilities/creative/exports/.+/epub$`))
  expect(print.href).toMatch(new RegExp(`^${origin}/api/capabilities/creative/exports/.+/print$`))
  const epubResponse=await fetch(epub.href);const epubBytes=new Uint8Array(await epubResponse.arrayBuffer())
  expect(epubResponse.status).toBe(200);expect(epubResponse.headers.get('content-type')).toContain('application/epub+zip')
  const archivePath=join(home,'downloaded.epub');await writeFile(archivePath,epubBytes)
  const mimetype=await run('unzip',['-p',archivePath,'mimetype']);const entries=await run('unzip',['-Z1',archivePath])
  expect(mimetype.stdout).toBe('application/epub+zip');expect(entries.stdout).toContain('META-INF/container.xml');expect(entries.stdout).toContain('OEBPS/content.opf')
  const pdfResponse=await fetch(print.href);const pdfBytes=new Uint8Array(await pdfResponse.arrayBuffer())
  expect(pdfResponse.status).toBe(200);expect(pdfResponse.headers.get('content-type')).toContain('application/pdf');expect(new TextDecoder().decode(pdfBytes.slice(0,5))).toBe('%PDF-')
  const listed=await (await fetch(origin+'/api/capabilities/creative/exports')).json()
  expect(listed.items).toHaveLength(1);expect(listed.items[0].metadata).toMatchObject({title:'UI Published Book',creator:'UI Author',language:'en-GB',identifier:'urn:uuid:ui-book'})
})

test('surfaces canonical-source failures without adding a placeholder receipt',async()=>{
  const before=await (await fetch(origin+'/api/capabilities/creative/exports')).json()
  render(<ManuscriptExports baseUrl={origin}/>)
  await waitFor(()=>expect(screen.queryAllByRole('article')).toHaveLength(before.items.length))
  change('Export source ID','missing-work');change('Export creator','UI Author');change('Export identifier','urn:uuid:missing')
  fireEvent.click(screen.getByRole('button',{name:'Create EPUB and print PDF'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('Work not found')
  const listed=await (await fetch(origin+'/api/capabilities/creative/exports')).json()
  expect(listed.items).toHaveLength(before.items.length)
  expect(screen.queryAllByRole('article')).toHaveLength(before.items.length)
})
