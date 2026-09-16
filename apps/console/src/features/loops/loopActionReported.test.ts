import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const F = (rel: string) => readFileSync(join(process.cwd(), "src/features", rel), 'utf8')
const strip = (s: string) =>
  s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const COCKPIT = F('loops/DesignCockpitPage.tsx')
const LIST = F('loops/LoopsListPage.tsx')
const SECTION = F('loop/LoopSection.tsx')

const WRITES: Array<[string, string, string[]]> = [
  ['loops/DesignCockpitPage.tsx', COCKPIT, ['uLoopAction', 'uLoopNudge', 'updateULoop']],
  ['loops/LoopsListPage.tsx', LIST, ['uLoopAction', 'deleteULoop']],
  ['loop/LoopSection.tsx', SECTION, ['uLoopAction', 'uLoopPlanStart']],
]

describe('a loop action that fails tells the user', () => {
  it('all three files use the SHARED reporter and keep no local copy', () => {
    for (const [name, src] of WRITES.map(([n, s]) => [n, strip(s)] as const)) {
      expect(src, `${name} must import the shared contract`).toMatch(
        /import \{ reportingWrite \} from '\.\.\/\.\.\/app\/shell\/reportingWrite'/,
      )
      const local = [...src.matchAll(/(function|const)\s+reportingWrite\b\s*[=(]/g)]
      expect(local.length, `${name}: a page-local copy would shadow the shared one`).toBe(0)
    }
  })

  it('no loop write swallows its rejection — the ratchet', () => {
    const offenders: string[] = []
    for (const [name, raw, calls] of WRITES) {
      const scan = strip(raw).replace(/=>/g, '⇒')
      for (const call of calls) {
        for (const m of scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
          if (/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/.test(scan.slice(m.index!, m.index! + 200))) {
            offenders.push(`${name}:${call}`)
          }
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('the ratchet is not vacuous — it finds every write it guards', () => {
    for (const [name, raw, calls] of WRITES) {
      for (const call of calls) {
        expect(strip(raw), `${name} should still perform api.${call}`).toContain(`api.${call}(`)
      }
    }
    const total = WRITES.reduce((n, [, raw]) => n + [...strip(raw).matchAll(/reportingWrite\(/g)].length, 0)
    expect(total, 'every write routed through the reporter').toBe(8)
  })

  it('EVERY write in the two refetching files is gated — not just the ones that already are', () => {
    for (const [name, raw] of [['cockpit', COCKPIT], ['list', LIST]] as const) {
      const src = strip(raw)
      const all = [...src.matchAll(/await reportingWrite\(/g)].length
      const gated = [...src.matchAll(/if \(!\(await reportingWrite\(/g)].length
      expect(all, `${name}: the sweep must find its writes`).toBeGreaterThan(0)
      expect(gated, `${name}: every write must gate what follows it`).toBe(all)
    }
    for (const [name, raw] of [['cockpit', COCKPIT], ['list', LIST]] as const) {
      const src = strip(raw)
      for (const m of src.matchAll(/if \(!\(await reportingWrite\(/g)) {
        const after = src.slice(m.index!, m.index! + 460)
        expect(after, `${name}: the guard must return`).toMatch(/\)\)\) return/)
        const guard = after.indexOf(')) return')
        const refetch = after.search(/loadLoop\(|loadTokens\(|invalidateKeys\(|refresh\(/)
        if (refetch > -1) expect(guard, `${name}: guard must precede the refetch`).toBeLessThan(refetch)
      }
    }
  })

  it('a failed nudge KEEPS the message and the panel open', () => {
    const body = strip(COCKPIT)
    const at = body.indexOf('async function sendNudge()')
    expect(at).toBeGreaterThan(-1)
    const fn = body.slice(at, body.indexOf('\n  }', at))
    const guard = fn.indexOf(')) return')
    const clear = fn.indexOf("setNudgeText('')")
    expect(guard, 'the nudge is reported').toBeGreaterThan(-1)
    expect(clear, 'and still cleared on success').toBeGreaterThan(-1)
    expect(guard, 'but only AFTER the write landed').toBeLessThan(clear)
    expect(fn, 'the panel closes on success too').toContain('setNudgeOpen(false)')
  })

  it('the created-loop route still NAVIGATES, deliberately', () => {
    const body = strip(SECTION)
    const kick = body.lastIndexOf('reportingWrite(')
    const nav = body.indexOf('navigate(kind === ', kick)
    expect(kick, 'the kick is reported').toBeGreaterThan(-1)
    expect(nav, 'and navigation follows it').toBeGreaterThan(kick)
    const between = body.slice(kick, nav)
    expect(between, 'no early return may gate the navigation').not.toMatch(/\breturn\b/)
  })

  it('every message names the action or the subject', () => {
    expect(COCKPIT).toContain('`${a} this loop`')
    expect(LIST).toContain('`${action} this loop`')
    expect(COCKPIT).toContain("'send that nudge'")
    expect(COCKPIT).toContain('`apply the ${scale} colour`')
    expect(COCKPIT).toContain('`set ${path}`')
    expect(SECTION).toContain("'start planning for this loop'")
  })

  it('the writes are still data-driven — the premise', () => {
    const src = strip(COCKPIT)
    for (const call of ['uLoopAction', 'updateULoop']) {
      const at = src.indexOf(`api.${call}(`)
      const before = src.slice(Math.max(0, at - 200), at)
      expect(before, `${call} gained an optimistic flip`).not.toMatch(/setStatus\(|setLoop\(/)
    }
  })
})
