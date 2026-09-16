import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) sources(p, out)
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

function writeMethods(): string[] {
  const src = readFileSync(join(SRC, 'shared/data/api.ts'), 'utf8')
  const decl =
    /\n {2}([a-zA-Z_][\w]*)\s*:\s*(?:async\s*)?\([^\n]*?\)\s*(?::[^=\n]*?)?=>\s*\n?\s*(get|post|put|patch|del)\b/g
  const out = new Set<string>()
  for (const m of src.matchAll(decl)) if (m[2] !== 'get') out.add(m[1])
  return [...out]
}

function functionSpans(s: string): [number, number, string][] {
  const head =
    /(?:async\s+function|function)\s+([A-Za-z_][\w]*)\s*\(|(?:const|let)\s+([A-Za-z_][\w]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::[^=]*?)?=>\s*\{/g
  const out: [number, number, string][] = []
  for (const m of s.matchAll(head)) {
    const from = s[m.index! + m[0].length - 1] === '{' ? m.index! + m[0].length - 1 : m.index! + m[0].length
    const open = s.indexOf('{', from)
    if (open < 0) continue
    let depth = 0
    for (let j = open; j < s.length; j++) {
      if (s[j] === '{') depth++
      else if (s[j] === '}') {
        depth--
        if (depth === 0) {
          out.push([open, j + 1, m[1] ?? m[2]])
          break
        }
      }
    }
  }
  return out
}

const CONFIRM = /\bawait\s+confirm(?:Delete)?\(/
const REPORTS =
  /reportingWrite\(|reportActionFailure\(|\.catch\(|notify\(|setToast|ne:toast|throw\b|set[A-Za-z]*(?:Err|Error|Msg|Message|Note|Detail|Result|Out|Failure)\b/

type Finding = { file: string; line: number; fn: string; method: string }

function unhandled(): { population: number; findings: Finding[] } {
  const writes = writeMethods()
  const pattern = `\\bapi\\.(${writes.sort((a, b) => b.length - a.length).join('|')})\\b`
  const HAS_WRITE = new RegExp(pattern)
  let population = 0
  const findings: Finding[] = []
  const seen = new Set<string>()
  for (const abs of sources(SRC)) {
    const rel = abs.slice(SRC.length + 1).replace(/\\/g, '/')
    if (rel === 'shared/data/api.ts') continue
    const s = readFileSync(abs, 'utf8')
    if (!CONFIRM.test(s) || !HAS_WRITE.test(s)) continue
    const spans = functionSpans(s)
    for (const m of s.matchAll(new RegExp(pattern, 'g'))) {
      const enclosing = spans.filter(([a, b]) => a <= m.index! && m.index! < b)
      if (!enclosing.length) continue
      const [a, b, fn] = enclosing.reduce((best, x) => (x[1] - x[0] < best[1] - best[0] ? x : best))
      const body = s.slice(a, b)
      if (!CONFIRM.test(body)) continue
      const key = `${rel}:${fn}:${m[1]}`
      if (seen.has(key)) continue
      seen.add(key)
      population++
      const delegated = new RegExp(`=>\\s*api\\.${m[1]}\\b`).test(body)
      if (delegated || REPORTS.test(body)) continue
      findings.push({ file: rel, line: s.slice(0, a).split('\n').length, fn, method: m[1] })
    }
  }
  return { population, findings }
}

describe('a write the user confirmed reports its failure', () => {
  const { population, findings } = unhandled()

  it('found the population (a scan that matches nothing passes every check it makes)', () => {
    expect(writeMethods().length, 'no write methods parsed from lib/api.ts').toBeGreaterThan(200)
    expect(
      population,
      'no confirm-gated writes found — the scan broke, it did not find a clean tree',
    ).toBeGreaterThan(30)
  })

  it('every one of them handles the failure', () => {
    expect(
      findings.map((f) => `${f.file}:${f.line} ${f.fn}() → api.${f.method}`),
      'These functions stop and ASK the user, then drop the failure on the floor — the dialog is ' +
        'consent for an action that may never have happened. Satisfy this in one of four ways:\n' +
        '  · wrap it: `if (!(await reportingWrite("delete the thing", () => api.x()))) return`\n' +
        '  · attach `.catch(reportActionFailure("…"))` when the caller needs the result\n' +
        '  · report in the catch through this surface\'s own error state\n' +
        '  · hand the write to a helper as a thunk, and report there\n' +
        'A `finally { setBusy(false) }` with no catch is NOT one of them: the rejection goes ' +
        'unhandled and the spinner stopping is what success looks like too.',
    ).toEqual([])
  })
})
