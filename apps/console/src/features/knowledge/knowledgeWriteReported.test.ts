import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = readFileSync(join(process.cwd(), "src/features/knowledge/KnowledgeListPage.tsx"), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

const WRITES = [
  'addToKnowledgeCollection',
  'removeFromKnowledgeCollection',
  'setKnowledgeReadState',
  'setKnowledgeFavorited',
  'updateKnowledgeCollection',
] as const

describe('a failed knowledge write says so, and does not refetch', () => {
  it('uses the SHARED reporter and keeps no local copy', () => {
    expect(CODE, 'the reporter must come from the shared module').toMatch(
      /import \{[^}]*\breportingWrite\b[^}]*\} from '\.\.\/\.\.\/app\/shell\/reportingWrite'/)
    const localDefs = [...CODE.matchAll(/(function|const)\s+(reportingWrite|reportActionFailure)\b\s*[=(]/g)]
    expect(localDefs.length, 'a page-local copy would shadow the shared one silently').toBe(0)
  })

  it('the create-shelf write reports too — it takes the module\'s OTHER form', () => {
    const at = CODE.indexOf('api.createKnowledgeCollection(')
    expect(at, 'the create-shelf call must still exist').toBeGreaterThan(-1)
    const region = CODE.slice(at, at + 320)
    expect(region, 'a failed create must be reported').toMatch(/reportActionFailure\(/)
    expect(region, 'and its follow-ups gated, so no cache invalidation or selection on a failure')
      .toMatch(/if \(!res\) return/)
    const gate = region.indexOf('if (!res) return')
    expect(region.indexOf('invalidateKeys'), 'invalidate must come AFTER the gate').toBeGreaterThan(gate)
    expect(region.indexOf('setCollectionTok'), 'and so must the selection').toBeGreaterThan(gate)
  })

  it('every one of the five writes routes through it', () => {
    const missing: string[] = []
    for (const call of WRITES) {
      const at = CODE.indexOf(`api.${call}(`)
      expect(at, `${call} must still be performed`).toBeGreaterThan(-1)
      const before = CODE.slice(Math.max(0, at - 220), at)
      if (!before.includes('reportingWrite(')) missing.push(call)
    }
    expect(missing, 'a swallowed data-driven write leaves nothing at all').toEqual([])
  })

  it('none of them swallows its rejection — the ratchet', () => {
    const scan = CODE.replace(/=>/g, '⇒')
    const offenders: string[] = []
    for (const call of WRITES) {
      for (const m of scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
        if (/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/.test(scan.slice(m.index!, m.index! + 200))) {
          offenders.push(`${call}:${scan.slice(0, m.index).split('\n').length}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('the refetch is GATED on the outcome, not run regardless', () => {
    const gates = [...CODE.matchAll(/const ok = await reportingWrite\(/g)]
    expect(gates.length, 'each write captures its outcome').toBe(WRITES.length)
    for (const m of gates) {
      const after = CODE.slice(m.index!, m.index! + 420)
      expect(after, 'the outcome must gate what follows').toMatch(/if \(!ok\) return/)
      const guard = after.indexOf('if (!ok) return')
      const refresh = after.search(/invalidateKeys\(|refreshItems\(|refreshCollections\(/)
      expect(refresh, 'the write is followed by a refetch').toBeGreaterThan(-1)
      expect(guard, 'and the guard comes BEFORE it').toBeLessThan(refresh)
    }
  })

  it('each message names WHICH item or shelf failed', () => {
    for (const frag of [
      'add "${it.title',
      'remove "${it.title',
      'mark "${it.title',
      'rename "${c.name}"',
    ]) {
      expect(SRC, `no message names the subject for ${frag}`).toContain(frag)
    }
    expect(SRC, 'the favourite toggle names both direction and subject').toMatch(
      /\$\{it\.favorited \? 'unfavourite' : 'favourite'\} "\$\{it\.title/,
    )
  })

  it('the writes are still data-driven — the premise of this contract', () => {
    for (const call of WRITES) {
      const at = CODE.indexOf(`api.${call}(`)
      expect(at, `${call} must still exist`).toBeGreaterThan(-1)
      const fnStart = CODE.lastIndexOf('async function ', at)
      expect(fnStart, `${call} must sit inside a handler`).toBeGreaterThan(-1)
      const before = CODE.slice(fnStart, at)
      expect(before, `${call} gained an optimistic flip`).not.toMatch(/set[A-Z]\w*\(/)
    }
  })
})
