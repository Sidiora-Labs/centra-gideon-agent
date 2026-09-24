import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { HEX, RAW_PX, PX_OK_CONTEXT, CALC_WITH_TOKEN, lineViolations, sourceViolations, scanTokenSource } from './tokenLintRule'

// vitest runs from web/, so the packaged JSON is two levels up.
const RULES_PATH = join(process.cwd(), "../../runtime/gideon/extensions/apps/token_lint_rules.json")

interface Rules {
  hex: string
  raw_px: string
  px_ok_context: string
  calc_with_token: string
}

function loadRules(): Rules {
  return JSON.parse(readFileSync(RULES_PATH, 'utf8')) as Rules
}

describe('token-lint rule parity (TS ↔ packaged JSON)', () => {
  const rules = loadRules()

  it('the canonical rule file was actually found and carries all four patterns', () => {
    expect(readFileSync(RULES_PATH, 'utf8').length, RULES_PATH).toBeGreaterThan(200)
    for (const k of ['hex', 'raw_px', 'px_ok_context', 'calc_with_token'] as const) {
      expect(typeof rules[k], `missing pattern: ${k}`).toBe('string')
      expect(rules[k].length, `empty pattern: ${k}`).toBeGreaterThan(5)
    }
  })

  it('every TS pattern is byte-identical to its canonical JSON source', () => {
    expect(HEX.source).toBe(rules.hex)
    expect(RAW_PX.source).toBe(rules.raw_px)
    expect(PX_OK_CONTEXT.source).toBe(rules.px_ok_context)
    expect(CALC_WITH_TOKEN.source).toBe(rules.calc_with_token)
  })

  it('the JSON patterns, recompiled, reach the same verdict as the TS rule', () => {
    const corpus: [string, ('hex' | 'px')[]][] = [
      ["  const c = '#1a2b3c'", ['hex']],
      ['  <div style={{ fontSize: 13px }}>', ['px']],
      ["  <div style={{ maxWidth: 'calc(var(--w) + 160px)' }}>", []],
      ["  <div style={{ maxWidth: 'calc(var(--content-width) + 160px)' }}>", ['px']],
      ['  <div style={{ gridTemplateColumns: minmax(0, 120px) }}>', []],
      ['  <div className="bg-surface-high text-on-surface">', []],
      ["  <div style={{ color: '#fff', padding: 4px }}>", ['hex', 'px']],
    ]
    const jsonHex = new RegExp(rules.hex)
    const jsonPx = new RegExp(rules.raw_px)
    const jsonOk = new RegExp(rules.px_ok_context)
    const jsonCalc = new RegExp(rules.calc_with_token)
    const viaJson = (line: string): ('hex' | 'px')[] => {
      const out: ('hex' | 'px')[] = []
      if (jsonHex.test(line)) out.push('hex')
      if (jsonPx.test(line) && !jsonCalc.test(line) && !jsonOk.test(line)) out.push('px')
      return out
    }
    for (const [line, expected] of corpus) {
      expect(lineViolations(line), `TS rule on: ${line}`).toEqual(expected)
      expect(viaJson(line), `JSON rule on: ${line}`).toEqual(expected)
    }
    expect(corpus.filter(([, e]) => e.length).length).toBeGreaterThanOrEqual(3)
  })
})

interface LexicalCase {
  name: string
  source: string
  expected: [number, 'hex' | 'px' | 'lexical'][]
  end_state?: 'code' | 'block_comment'
  intended?: [number, 'hex' | 'px' | 'lexical'][]
}

const lexicalCases = JSON.parse(readFileSync(join(process.cwd(), '../../checks/fixtures/token_lint_cases.json'), 'utf8')) as LexicalCase[]

describe('shared token-lint lexical corpus', () => {
  it.each(lexicalCases)('$name', ({ name, source, expected, intended, end_state = 'code' }) => {
    expect(scanTokenSource(source).end_state).toBe(end_state)
    const actual = sourceViolations(source).map((hit) => {
      const [line, detail] = hit.split(': ')
      return [Number(line), detail.split(' — ')[0]]
    })
    expect(actual).toEqual(expected)
    if (name.startsWith('gap_')) {
      expect(intended).toBeDefined()
      expect(actual).not.toEqual(intended)
    }
  })

  it('keeps block-comment state local to each file', () => {
    sourceViolations('/* unfinished')
    expect(sourceViolations("const color = '#fff'")).toHaveLength(1)
  })

  it('counts real clean and rejected sources without known gaps', () => {
    const normalCases = lexicalCases.filter(({ name }) => !name.startsWith('gap_'))
    expect(lexicalCases.length - normalCases.length).toBeGreaterThanOrEqual(2)
    let clean = 0
    let rejected = 0
    for (const { source, expected } of normalCases) {
      const hits = sourceViolations(source)
      expect(hits.length > 0).toBe(expected.length > 0)
      if (hits.length) rejected++
      else clean++
    }
    expect(clean).toBeGreaterThanOrEqual(10)
    expect(rejected).toBeGreaterThanOrEqual(15)
  })

})
