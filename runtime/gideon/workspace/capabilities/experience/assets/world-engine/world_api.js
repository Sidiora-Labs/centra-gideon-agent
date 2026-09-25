import { Database } from 'bun:sqlite'
import { createHash } from 'node:crypto'
import { join } from 'node:path'
export async function worldAPI(root, data) {
  const { getWorld, worldExists } = await import(join(root, 'server/world.ts'))
  const { runVerb } = await import(join(root, 'server/verbs.ts'))
  const db = new Database(join(data, 'gideon-operations.sqlite'), { create: true })
  db.run('CREATE TABLE IF NOT EXISTS requests(world TEXT,id TEXT,hash TEXT,receipt TEXT,PRIMARY KEY(world,id))')
  const seq = w => Math.max(w.snapSeq, w.entries.at(-1)?.seq ?? -1)
  return async request => {
    const match = new URL(request.url).pathname.match(/^\/gideon\/worlds\/([a-zA-Z0-9_-]{1,64})$/)
    if (!match) return Response.json({ error: 'Invalid world route' }, { status: 400 })
    const world = match[1]
    if (!worldExists(world)) return Response.json({ error: 'World has not been opened' }, { status: 404 })
    const w = getWorld(world)
    if (request.method === 'GET') return Response.json({ world, seq: seq(w), state: w.state, present: [...w.clients].map(c => ({ id: c.id, avatar: c.avatar, pose: c.lastPose, agent: !!c.agent })) })
    const body = await request.json()
    if (!body || typeof body !== 'object' || !Array.isArray(body.operations) || body.operations.length > 40 || !Number.isInteger(body.expected_seq) || typeof body.request_id !== 'string' || !/^[a-zA-Z0-9_-]{1,96}$/.test(body.request_id)) return Response.json({ error: 'Invalid world operation' }, { status: 400 })
    if (body.operations.some(op => !op || !['spawn','place','remove','comp'].includes(op.verb) || !op.args || typeof op.args !== 'object')) return Response.json({ error: 'Unsupported world operation' }, { status: 400 })
    const hash = createHash('sha256').update(JSON.stringify(body.intent ?? body)).digest('hex')
    const prior = db.query('SELECT hash,receipt FROM requests WHERE world=? AND id=?').get(world, body.request_id)
    if (prior) return prior.hash !== hash ? Response.json({ error: 'Request identifier was reused' }, { status: 409 }) : prior.receipt ? new Response(prior.receipt, { headers: { 'Content-Type': 'application/json' } }) : Response.json({ error: 'Prior operation interrupted; inspect canonical world before retrying with a new request' }, { status: 409 })
    if (seq(w) !== body.expected_seq) return Response.json({ error: 'World changed; refresh before editing', seq: seq(w) }, { status: 409 })
    for (const op of body.operations) {
      if (op.verb === 'spawn' && w.state.entities[op.args.id]) return Response.json({ error: 'Object already exists' }, { status: 409 })
      if (['place','remove'].includes(op.verb) && !w.state.entities[op.args.id]) return Response.json({ error: 'Object does not exist' }, { status: 409 })
    }
    const c = [...w.clients].find(c => c.id === 'gideon' && !c.spectator)
    if (!c) return Response.json({ error: 'Gideon world presence is disconnected' }, { status: 409 })
    db.run('INSERT INTO requests VALUES(?,?,?,NULL)', world, body.request_id, hash)
    const applied = []
    for (const op of body.operations) {
      const before = seq(w)
      runVerb({ w, c, now: Date.now() }, op.verb, op.args)
      if (seq(w) === before) break
      applied.push({ verb: op.verb, id: op.args.id, seq: seq(w) })
    }
    const receipt = { world, request_id: body.request_id, seq: seq(w), operations: applied, complete: applied.length === body.operations.length }
    db.run('UPDATE requests SET receipt=? WHERE world=? AND id=?', JSON.stringify(receipt), world, body.request_id)
    return Response.json(receipt)
  }
}
