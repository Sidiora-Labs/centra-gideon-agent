import ts from 'typescript'
import { nodes } from '../../shared/testing/sourceOwners'
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
      const calls = nodes(src, node => ts.isCallExpression(node) && node.expression.getText() === 'notify') as ts.CallExpression[]
      for (const call of calls) {
        let parent: ts.Node | undefined = call.parent
        let rejection = false
        while (parent) {
          if (ts.isCatchClause(parent) || ts.isJsxAttribute(parent) && parent.name.getText() === 'onError') rejection = true
          parent = parent.parent
        }
        if (!rejection && !FAILURE_WORDS.test(call.arguments[0]?.getText() ?? '')) continue
        failureCalls++
        const level = call.arguments[1]
        if (!level || !ts.isStringLiteral(level) || level.text !== 'error') {
          const line = call.getSourceFile().getLineAndCharacterOfPosition(call.getStart()).line + 1
          offenders.push(`${name}:${line} — ${call.getText()}`)
        }
      }
    }
    expect(offenders, `a failure that looks like an info toast gets missed:\n${offenders.join('\n')}`).toEqual([])
    expect(failureCalls, 'the scan must actually find the failure calls').toBeGreaterThanOrEqual(14)
  })
})
