import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src/features/ChatPage.tsx")
const raw = readFileSync(SRC, 'utf8')
const scan = raw.replace(/=>/g, '⇒')

const STOPS = ['stopChat', 'cancelFanout', 'cancelQueued', 'interruptChat', 'cancelRetag'] as const

describe('a failed cancel tells the user the work did not stop', () => {
  it('the reporter is the SHARED one, imported, and defined exactly ONCE in the tree', () => {
    expect(raw, 'the shared sentence is imported').toMatch(
      /import \{ reportActionFailure, reportingWrite \} from '\.\.\/app\/shell\/reportingWrite'/,
    )
    const shared = readFileSync(join(process.cwd(), "src/app/shell/reportingWrite.ts"), 'utf8')
    expect(shared).toMatch(/^export const reportActionFailure = \(what: string\) => \(e: unknown\) => \{$/m)
    expect(shared).toContain("notify(failureSentence(what, e), 'error')")
    expect(shared, 'the sentence has exactly one composer').toMatch(
      /function failureSentence\(what: string, e: unknown\): string/,
    )
    expect(shared, 'and it filters the unusable text rather than printing it')
      .toMatch(/const detail = readableErrText\(e\)/)
    const walk = (dir: string, out: string[] = []): string[] => {
      for (const name of readdirSync(dir)) {
        const abs = join(dir, name)
        if (statSync(abs).isDirectory()) walk(abs, out)
        else if (/\.tsx?$/.test(name) && !name.includes('.test.')) out.push(abs)
      }
      return out
    }
    const defs = walk(join(process.cwd(), "src")).filter((abs) =>
      /(^|\s)(const|function)\s+reportActionFailure\b\s*[=(]/m.test(readFileSync(abs, 'utf8')),
    )
    expect(defs.map((d) => d.replace(process.cwd() + '/', '')), 'exactly one home').toEqual([
      'src/app/shell/reportingWrite.ts',
    ])
  })

  it('every cancel/stop write routes through it', () => {
    const missing: string[] = []
    for (const call of STOPS) {
      for (const m of scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
        const chain = scan.slice(m.index!, m.index! + 200)
        if (!/\.catch\(reportActionFailure\('/.test(chain)) {
          missing.push(`${call}:${scan.slice(0, m.index).split('\n').length}`)
        }
      }
    }
    expect(missing, 'a cancel that swallows tells the user the work stopped when it did not').toEqual([])
  })

  it('no cancel/stop write swallows its rejection — the ratchet', () => {
    const offenders: string[] = []
    for (const call of STOPS) {
      for (const m of scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
        const chain = scan.slice(m.index!, m.index! + 200)
        if (/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/.test(chain)) {
          offenders.push(`${call}:${scan.slice(0, m.index).split('\n').length}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('the ratchet is not vacuous — it finds all six sites', () => {
    const found = STOPS.flatMap((c) => [...scan.matchAll(new RegExp(`api\\.${c}\\(`, 'g'))])
    expect(found.length, 'the six known cancel sites must be in scope').toBeGreaterThanOrEqual(6)
    expect(new Set(STOPS.filter((c) => scan.includes(`api.${c}(`))).size).toBe(STOPS.length)
  })

  it('each message names WHICH stop failed', () => {
    for (const what of [
      'stop this turn',
      'cancel the subagents',
      'cancel that queued message',
      'interrupt this turn',
      'cancel the retag run',
    ]) {
      expect(raw, `no report says ${what}`).toContain(`reportActionFailure('${what}')`)
    }
  })

  it('the optimistic flips are still there — the premise of the whole finding', () => {
    expect(raw, 'stop() still claims the turn ended before the call').toMatch(
      /markStreaming\(false\)\s*\n\s*if \(sessionRef\.current\) await api\.stopChat/,
    )
    expect(raw, 'the queue row still vanishes first').toMatch(
      /setQueued\(\(prev\) => prev\.filter\(\(q\) => q\.id !== id\)\); const s = sessionRef\.current/,
    )
    expect(raw, 'the subagent cards still read cancelled first').toContain("error: 'cancelled'")
  })

  it('no revert was added — the decision, pinned', () => {
    const shared = readFileSync(join(process.cwd(), "src/app/shell/reportingWrite.ts"), 'utf8')
    const at = shared.indexOf('export const reportActionFailure =')
    expect(at, 'the reporter must exist').toBeGreaterThan(-1)
    const body = shared.slice(at, shared.indexOf('\n}', at) + 2)
    expect(body, 'the reporter reports').toContain('notify(')
    for (const forbidden of ['markStreaming(', 'setQueued(', 'setSubagents(', 'setSelection(']) {
      expect(body, `the reporter must not also mutate state — found ${forbidden}`).not.toContain(
        forbidden,
      )
    }
    for (const call of STOPS) {
      for (const m of scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
        const chain = scan.slice(m.index!, m.index! + 200)
        expect(chain, `${call} added a revert`).not.toContain('markStreaming(true)')
      }
    }
  })
})
