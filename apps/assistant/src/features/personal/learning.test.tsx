import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer as createNetServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterAll, describe, expect, it } from 'vitest'

const root = resolve(process.cwd(), '../..')
const processes: ChildProcessWithoutNullStreams[] = []
const directories: string[] = []

afterAll(async () => {
  for (const child of processes) child.kill('SIGTERM')
  for (const directory of directories) await rm(directory, { recursive: true, force: true })
})

async function port(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>(ready => server.listen(0, '127.0.0.1', ready))
  const address = server.address()
  const selected = typeof address === 'object' && address ? address.port : 0
  await new Promise<void>(closed => server.close(() => closed()))
  return selected
}

const serverScript = String.raw`
import asyncio, json, os, sys
from pathlib import Path
from types import SimpleNamespace
from aiohttp import web
import gideon.core.config.loader as loader
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.cognition.learning import proposals
from gideon.interfaces.dashboard.handlers import capabilities_knowledge_capture, capabilities_knowledge_reviews, learning

async def main(port):
  home=Path(os.environ['GIDEON_HOME']);loader.config_dir=lambda:home
  (home/'config.json').write_text(json.dumps({'learning':{'enabled':True}}),encoding='utf-8')
  store=KnowledgeStore(str(home/'knowledge.sqlite3'))
  state=SimpleNamespace(knowledge_store=store,context_builder=None)
  app=web.Application();app['state']=state
  capabilities_knowledge_capture.register(app);capabilities_knowledge_reviews.register(app);learning.register_learning_routes(app)
  _, proposal=proposals.enqueue(kind='skill',title='Review gate record',body='A native proposal for the reviewer gate test.',provenance='capture',source_excerpt='Native evidence excerpt',evidence_refs=['capture:learning-test'],evidence_strength='direct',occurrences=1,min_evidence=1)
  if proposal is None: raise RuntimeError('Native proposal fixture was not filed')
  runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',int(port));await site.start()
  print(json.dumps({'proposal_id':proposal.id}),flush=True)
  try: await asyncio.Event().wait()
  finally: await runner.cleanup();store.db.close()

asyncio.run(main(int(sys.argv[1])))
`

async function startServer(): Promise<{ origin: string; proposalId: string }> {
  const home = await mkdtemp(join(tmpdir(), 'gideon-learning-native-home-')); directories.push(home)
  const selected = await port()
  const child = spawn(process.env.GIDEON_TEST_PYTHON || 'python3', ['-c', serverScript, String(selected)], {
    cwd: root, env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: join(root, 'runtime') },
  })
  processes.push(child)
  const line = await new Promise<string>((ready, fail) => {
    let output = ''; let errors = ''
    const timeout = setTimeout(() => fail(new Error(`Native Learning server timed out: ${errors}`)), 20000)
    child.stdout.on('data', chunk => { output += String(chunk); if (output.includes('\n')) { clearTimeout(timeout); ready(output.split('\n')[0]) } })
    child.stderr.on('data', chunk => { errors += String(chunk) })
    child.once('exit', code => { clearTimeout(timeout); fail(new Error(`Native Learning server exited ${code}: ${errors}`)) })
  })
  return { origin: `http://127.0.0.1:${selected}`, proposalId: (JSON.parse(line) as { proposal_id: string }).proposal_id }
}

describe('Learning native records and review gate', () => {
  it('retains capture history and denies proposal decisions without a human dashboard actor', async () => {
    const native = await startServer()
    const create = await fetch(`${native.origin}/api/capabilities/knowledge/captures`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ request_id: 'learning-receipt-001', text: 'A real native capture receipt.' }),
    })
    expect(create.status).toBe(200)
    const receipt = await create.json() as Record<string, unknown>
    expect(receipt.status).toBe('needs_review')
    expect(receipt.text).toBe('A real native capture receipt.')
    expect(receipt.id).toEqual(expect.any(String))
    expect(receipt.revision).toBe(1)

    const historyResponse = await fetch(`${native.origin}/api/capabilities/knowledge/captures?limit=20&offset=0`)
    expect(historyResponse.status).toBe(200)
    const history = await historyResponse.json() as { items: Array<Record<string, unknown>>; total: number }
    expect(history.total).toBe(1)
    expect(history.items[0]).toMatchObject({ id: receipt.id, request_id: 'learning-receipt-001', text: 'A real native capture receipt.', status: 'needs_review', revision: 1 })

    const decision = await fetch(`${native.origin}/api/learning/proposals/${encodeURIComponent(native.proposalId)}/accept`, { method: 'POST' })
    expect(decision.status).toBe(403)
    expect(await decision.json()).toMatchObject({ error: expect.stringMatching(/human/i) })
    const inbox = await fetch(`${native.origin}/api/learning/proposals`)
    expect(inbox.status).toBe(200)
    const pending = await inbox.json() as { rows: Array<{ id: string; status: string; evidence_refs: string[] }> }
    expect(pending.rows).toContainEqual(expect.objectContaining({ id: native.proposalId, status: 'pending', evidence_refs: ['capture:learning-test'] }))
  }, 30000)
})
