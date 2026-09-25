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
const frontendPort=39000+Math.floor(Math.random()*10000)
const evidence=await mkdtemp(resolve(tmpdir(),'gideon-full-console-browser-'))
const runtime=spawn(process.env.GIDEON_TEST_PYTHON || '/tmp/gideon-runtime-venv/bin/python', ['checks/capabilities/full_server.py'], {
  cwd:root,env:{...process.env,PYTHONPATH:resolve(root,'runtime'),GIDEON_HOME:resolve(evidence,'home'),GIDEON_SKIP_APP_BACKENDS:'1',GIDEON_AUTH_MODE:'local_token',GIDEON_BROWSER_ORIGIN:`http://127.0.0.1:${frontendPort}`},stdio:['ignore','pipe','pipe']})
let backendLog=''
runtime.stderr.on('data',chunk=>backendLog+=chunk.toString())
let server,browser,page
try {
  const ready=await new Promise((accept,reject)=>{
    const timeout=setTimeout(()=>reject(new Error('Runtime did not start: '+backendLog)),60000)
    createInterface({input:runtime.stdout}).on('line',line=>{try { const value=JSON.parse(line);if(value.port){clearTimeout(timeout);accept(value)}} catch {}})
    runtime.once('exit',code=>{clearTimeout(timeout);reject(new Error('Runtime exited '+code+': '+backendLog))})
  })
  server=await createServer({configFile:false,root:resolve(root,'apps/console'),plugins:[react(),tailwindcss(),{
    name:'full-console-journey-entry',configureServer(dev){dev.middlewares.use('/capability-journey',async(req,res)=>{
      res.setHeader('Content-Type','text/html')
      res.end(await dev.transformIndexHtml('/capability-journey',`<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@fs/${resolve(root,'checks/capabilities/full_entry.tsx')}"></script></body></html>`))
    })}
  }],server:{host:'127.0.0.1',port:frontendPort,strictPort:true,fs:{allow:[root]},proxy:{'/api':{target:`http://127.0.0.1:${ready.port}`,changeOrigin:true}}}})
  await server.listen()
  const base=`http://127.0.0.1:${server.httpServer.address().port}`
  browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE || '/opt/chromium/chrome-linux64/chrome',headless:true,args:['--no-sandbox']})
  page=await browser.newPage({viewport:{width:1440,height:960}})
  page.setDefaultNavigationTimeout(120000)
  page.setDefaultTimeout(60000)
  const errors=[]
  page.on('pageerror',error=>errors.push(error.message))
  await page.goto(base+'/api/capabilities/identity/stories?token='+encodeURIComponent(ready.token))
  const configured=await page.request.put(base+'/api/dashboard/config',{data:{user_name:'Capability Operator'},headers:{Origin:base}})
  assert.equal(configured.status(),200,await configured.text())
  await page.goto(base+'/capability-journey#/capabilities/media', { waitUntil: 'domcontentloaded' })
  await page.getByRole('heading',{name:'Image sketches',exact:true}).waitFor()
  await page.keyboard.press('Control+k')
  await page.getByRole('searchbox',{name:'Search pages and actions'}).fill('Voice controls')
  await page.getByRole('option').filter({hasText:'Voice controls'}).click()
  const voiceDialog=page.getByRole('dialog',{name:'Voice controls'})
  await voiceDialog.waitFor()
  await voiceDialog.getByRole('textbox',{name:'Navigation command'}).fill('Capabilities')
  await voiceDialog.getByRole('button',{name:'Go',exact:true}).click()
  await voiceDialog.getByText('Opened capabilities',{exact:true}).waitFor()
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
  const exported=page.getByRole('link',{name:'Download PNG',exact:true})
  await exported.first().waitFor()
  const downloadResponse=await page.request.get(new URL(await exported.first().getAttribute('href'),base).href)
  assert.equal(downloadResponse.status(),200)
  assert.equal((await downloadResponse.body()).subarray(0,8).toString('hex'),'89504e470d0a1a0a')
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
    process.stdout.write('Opening capability '+lane+'\n')
    const [response]=await Promise.all([
      page.waitForResponse(response=>response.url().includes('/api/capabilities/'+lane) && response.request().method()==='GET'),
      links.nth(index).click(),
    ])
    assert.equal(response.status(),200,'Area request failed: '+response.url())
    await page.waitForURL(url=>url.hash.split('?')[0]===href)
    await page.waitForFunction(lane=>document.querySelector('nav[aria-label="Capabilities"] a[aria-current="page"]')?.getAttribute('href')==='#/capabilities/'+lane,lane)
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
  if(page){await page.screenshot({path:resolve(evidence,'failure.png'),fullPage:true}).catch(()=>{});await writeFile(resolve(evidence,'page.txt'),await page.locator('body').innerText().catch(()=>''))}
  await writeFile(resolve(evidence,'failure.json'),JSON.stringify({error:String(error),url:page?.url(),backendLog},null,2))
  process.stderr.write(JSON.stringify({status:'failed',evidence,error:String(error)})+'\n')
  process.exitCode=1
} finally {
  if(browser)await browser.close()
  if(server)await server.close()
  runtime.kill('SIGTERM')
  await writeFile(resolve(evidence,'runtime.log'),backendLog)
}
