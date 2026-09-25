import { readFileSync, mkdirSync } from 'node:fs'
import { resolve, join } from 'node:path'
import { createHash, createHmac, timingSafeEqual } from 'node:crypto'
const data = process.env.GIDEON_APP_DATA_DIR
const secret = process.env.GIDEON_APP_SECRET
if (!data || !secret) throw new Error('World engine requires app storage and proxy credentials')
const config = JSON.parse(readFileSync(join(data, 'engine.json'), 'utf8'))
if (Object.keys(config).sort().join(',') !== 'video,worlds') throw new Error('Operator configuration requires worlds and video paths')
const pins = { worlds: 'bf9e0231795d55297f976948e2a79de3e1320e9d', video: '2d152b9ae4e12e9ec152ea088850e377c319d4d4' }
for (const kind of ['worlds', 'video']) {
  if (typeof config[kind] !== 'string' || !config[kind].startsWith('/')) throw new Error('Engine paths must be absolute')
  config[kind] = resolve(config[kind])
  const result = Bun.spawnSync(['git', '-C', config[kind], 'rev-parse', 'HEAD'])
  if (result.exitCode !== 0 || result.stdout.toString().trim() !== pins[kind]) throw new Error('Installed engine dependency revision mismatch')
}
for (const directory of ['worlds', 'assets', 'relay']) mkdirSync(join(data, directory), { recursive: true })
Object.assign(process.env, { WORLDS_DIR: join(data, 'worlds'), OPT_DIR: join(data, 'assets'), RELAY_STATE_DIR: join(data, 'relay'), EIDOVERSE_DIR: config.video, SKIP_OPT_SWEEP: '1', JOIN_TOKEN: '' })
const originalServe = Bun.serve.bind(Bun)
Bun.serve = options => originalServe({ ...options, hostname: '127.0.0.1', async fetch(request, server) {
  const url = new URL(request.url)
  if (url.pathname === '/health') return new Response('World engine process active')
  const provided = request.headers.get('X-Gideon-Proxy') || ''
  const [timestamp, mac] = provided.split(':')
  if (!/^\d+$/.test(timestamp || '') || !/^[a-f0-9]{64}$/.test(mac || '') || Math.abs(Date.now() / 1000 - Number(timestamp)) > 60) return new Response('Proxy admission required', { status: 403 })
  const bytes = new Uint8Array(await request.clone().arrayBuffer())
  const hash = createHash('sha256').update(bytes).digest('hex')
  const expected = createHmac('sha256', secret).update(`${timestamp}:${request.method}:${url.pathname}${url.search}:${hash}`).digest()
  if (!timingSafeEqual(expected, Buffer.from(mac, 'hex'))) return new Response('Proxy admission rejected', { status: 403 })
  return options.fetch(request, server)
} })
await import(join(config.worlds, 'server', 'server.ts'))
