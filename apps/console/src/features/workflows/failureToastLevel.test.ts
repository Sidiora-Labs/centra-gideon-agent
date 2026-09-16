import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

const DIR = join(process.cwd(), "src/features/workflows")
const FAILURE_WORDS = /Could not |Couldn't | failed| rejected/

describe('workflow failure toasts carry the error level', () => {
  it('every failure-worded notify passes error', () => {
    const offenders: string[] = []
    let failureCalls = 0
    for (const name of readdirSync(DIR)) {
      if (!/\.tsx?$/.test(name) || /\.test\./.test(name)) continue
      const src = readFileSync(join(DIR, name), 'utf8')
      for (const m of src.matchAll(/notify\(/g)) {
        let i = m.index! + m[0].length
        let depth = 1
        while (i < src.length && depth > 0) {
          if (src[i] === '(') depth++
          else if (src[i] === ')') depth--
          i++
        }
        const args = src.slice(m.index! + m[0].length, i - 1)
        if (!FAILURE_WORDS.test(args)) continue
        failureCalls++
        const line = src.slice(0, m.index).split('\n').length
        if (!/'error'/.test(args)) offenders.push(`${name}:${line} — notify(${args.slice(0, 60)}…)`)
      }
    }
    expect(offenders, `a failure that looks like an info toast gets missed:\n${offenders.join('\n')}`).toEqual([])
    expect(failureCalls, 'the scan must actually find the failure calls').toBeGreaterThanOrEqual(14)
  })
})
