import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { chromium } from 'playwright'
import { createRequire } from 'node:module'
const consoleRequire = createRequire(new URL('../../apps/console/package.json', import.meta.url))
const { createServer } = await import(consoleRequire.resolve('vite'))
const { default: react } = await import(consoleRequire.resolve('@vitejs/plugin-react'))
const { default: tailwindcss } = await import(consoleRequire.resolve('@tailwindcss/vite'))
import assert from 'node:assert/strict'

const root=process.cwd()
const evidence=await mkdtemp(resolve(tmpdir(),'gideon-capability-browser-'))
const runtime=spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', ['checks/capabilities/browser_server.py'], {
  cwd:root,env:{...process.env,PYTHONPATH:resolve(root,'runtime'),GIDEON_HOME:resolve(evidence,'home'),GIDEON_SKIP_APP_BACKENDS:'1'},stdio:['ignore','pipe','pipe']})
let backendLog=''
runtime.stderr.on('data',chunk=>backendLog+=chunk.toString())
let server,browser
try {
  const ready=await new Promise((accept,reject)=>{
    const timeout=setTimeout(()=>reject(new Error('Runtime did not start: '+backendLog)),60000)
    createInterface({input:runtime.stdout}).on('line',line=>{try { const value=JSON.parse(line);if(value.port){clearTimeout(timeout);accept(value)}} catch {}})
    runtime.once('exit',code=>{clearTimeout(timeout);reject(new Error('Runtime exited '+code+': '+backendLog))})
  })
  server=await createServer({configFile:false,root:resolve(root,'apps/console'),plugins:[react(),tailwindcss(),{
    name:'capability-journey-entry',configureServer(dev){dev.middlewares.use('/capability-journey',async(req,res)=>{
      res.setHeader('Content-Type','text/html')
      res.end(await dev.transformIndexHtml('/capability-journey',`<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@fs/${resolve(root,'checks/capabilities/browser_entry.tsx')}"></script></body></html>`))
    })}
  }],server:{host:'127.0.0.1',port:0,fs:{allow:[root]},proxy:{'/api':{target:`http://127.0.0.1:${ready.port}`,changeOrigin:true}}}})
  await server.listen()
  const base=`http://127.0.0.1:${server.httpServer.address().port}`
  browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE || '/opt/chromium/chrome-linux64/chrome',headless:true,args:['--no-sandbox']})
  const page=await browser.newPage({viewport:{width:1440,height:960}})
  const errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.goto(base+'/api/capabilities/identity/stories?token='+encodeURIComponent(ready.token))
  await page.goto(base+'/capability-journey#/capabilities/media')
  await page.getByRole('button',{name:'Voice controls',exact:true}).click()
  const voiceDialog=page.getByRole('dialog',{name:'Voice controls'})
  await voiceDialog.waitFor()
  await voiceDialog.getByRole('textbox',{name:'Navigation command'}).fill('Personal identity')
  await voiceDialog.getByRole('button',{name:'Go',exact:true}).click()
  await voiceDialog.getByText('Opened capabilities/identity',{exact:true}).waitFor()
  await page.keyboard.press('Escape')
  await voiceDialog.waitFor({state:'hidden'})
  await page.getByRole('link',{name:'Visual media',exact:true}).click()

  await page.getByRole('button',{name:'New sketch',exact:true}).click()
  const canvas=page.locator('canvas')
  await canvas.waitFor()
  const box=await canvas.boundingBox()
  assert.ok(box && box.width>0)
  await page.mouse.move(box.x+30,box.y+30)
  await page.mouse.down()
  await page.mouse.move(box.x+130,box.y+80,{steps:12})
  await page.mouse.up()
  await page.getByRole('button',{name:'Save',exact:true}).click()
  await page.getByRole('button',{name:/Export PNG/}).click()
  const exported=page.getByRole('link',{name:/Download|Open exported|PNG/i})
  await exported.first().waitFor()
  const sketchID=new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('sketch')
  assert.ok(sketchID)
  let response=await page.request.get(base+'/api/capabilities/media/sketches/'+sketchID)
  assert.equal(response.status(),200)
  assert.equal((await response.json()).strokes.length,1)
  await page.reload()
  await canvas.waitFor()
  await page.screenshot({path:resolve(evidence,'media-desktop.png'),fullPage:true})
  const nav=page.getByRole('navigation',{name:'Capabilities',exact:true})
  const links=nav.getByRole('link')
  assert.equal(await links.count(),10)
  for(let index=0;index<10;index++){
    const href=await links.nth(index).getAttribute('href')
    const lane=href.split('/').at(-1)
    const reply=page.waitForResponse(response=>response.url().includes('/api/capabilities/'+lane) && response.request().method()==='GET')
    await links.nth(index).click()
    const response=await reply
    assert.equal(response.status(),200,'Area request failed: '+response.url())
    await page.waitForFunction(()=>!document.querySelector('[role="status"]')?.textContent?.includes('Loading'))
    assert.ok(new URL(page.url()).hash.startsWith('#/capabilities/'))
  }
  await page.setViewportSize({width:390,height:844})
  await nav.getByRole('link',{name:'Visual media',exact:true}).click()
  await page.screenshot({path:resolve(evidence,'media-mobile.png'),fullPage:true})
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth)
  assert.equal(overflow,false,'Capability shell overflows the mobile viewport')
  assert.deepEqual(errors,[])
  await writeFile(resolve(evidence,'evidence.json'),JSON.stringify({status:'passed',sketch_id:sketchID,checks:['actual dialog navigation and applied receipt','real canvas pointer/save/export/reload','ten assembled routes with actual HTTP','mobile shell geometry'],errors},null,2))
  process.stdout.write(JSON.stringify({status:'passed',evidence})+'\n')
} catch(error){
  await writeFile(resolve(evidence,'failure.json'),JSON.stringify({error:String(error),backendLog},null,2))
  process.stderr.write(JSON.stringify({status:'failed',evidence,error:String(error)})+'\n')
  process.exitCode=1
} finally {
  if(browser)await browser.close()
  if(server)await server.close()
  runtime.kill('SIGTERM')
  await writeFile(resolve(evidence,'runtime.log'),backendLog)
}
