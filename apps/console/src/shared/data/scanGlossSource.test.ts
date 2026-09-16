import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SCAN_RULE_GLOSS, ruleGloss } from './scanFindings'


const GLOSS_JSON = join(process.cwd(), "../../runtime/gideon/security/scan_rule_gloss.json")

describe('the gloss map this build actually carries', () => {
  it('is populated — an empty map would be a silent, green regression', () => {
    expect(Object.keys(SCAN_RULE_GLOSS).length, 'the map must not be reachable-but-empty')
      .toBeGreaterThanOrEqual(15)
    for (const [rule, text] of Object.entries(SCAN_RULE_GLOSS)) {
      expect(typeof text, `${rule} must gloss to a string`).toBe('string')
      expect(text.length, `${rule} has an empty gloss`).toBeGreaterThan(0)
    }
  })

  it('came from the shared JSON, not from a literal in this module', () => {
    const raw = JSON.parse(readFileSync(GLOSS_JSON, 'utf8')) as Record<string, unknown>
    const expected = Object.fromEntries(
      Object.entries(raw).filter(
        (e): e is [string, string] => !e[0].startsWith('_') && typeof e[1] === 'string',
      ),
    )
    expect(SCAN_RULE_GLOSS).toEqual(expected)
  })

  it('never serves the file’s own rationale as a rule', () => {
    expect(ruleGloss('_comment'), 'metadata is not a rule').toBe('')
    expect(Object.keys(SCAN_RULE_GLOSS).some((k) => k.startsWith('_'))).toBe(false)
  })

  it('answers empty for an unknown rule rather than echoing the name', () => {
    expect(ruleGloss('no_such_rule_exists')).toBe('')
    expect(ruleGloss('')).toBe('')
    expect(ruleGloss('python_exec')).toBe('This code runs an external program on your machine.')
  })
})
