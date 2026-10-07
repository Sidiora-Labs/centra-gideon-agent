import ts from 'typescript'
import { apiCalls, nodes, callStatement, namedOwner } from '../../shared/testing/sourceOwners'
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = readFileSync(join(process.cwd(), "src/features/ChatPage.tsx"), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const SELECTION_WRITES = [
  'setSessionAcpAgent', 'setSessionAgent', 'setSessionModel',
  'setApprovalMode', 'setTaskMode', 'setReasoningEffort',
  'setSessionNaturalVoice',
]

describe('a selection that fails to persist tells the user', () => {
  it('the reporter exists and carries the server’s message', () => {
    expect(CODE).toMatch(/const persistSelection = <T,>\(what: string, p: Promise<T>\)/)
    expect(CODE).toMatch(/notify\(`Couldn't apply \$\{what\} to this session: \$\{String\(\(e as Error\)\?\.message \|\| e\)\}`, 'error'\)/)
  })

  it('no selection write swallows its rejection', () => {
    const offenders: string[] = []
    for (const call of SELECTION_WRITES) {
      for (const m of CODE.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
        const chain = CODE.slice(m.index!, m.index! + 320)
        if (/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(chain)) offenders.push(`${call} @${CODE.slice(0, m.index).split('\n').length}`)
      }
    }
    expect(offenders, 'a swallowed selection write leaves the composer lying').toEqual([])
  })

  it('every selection write goes through an actual failure reporter', () => {
    const calls = SELECTION_WRITES.flatMap(method => apiCalls(CODE, method))
    expect(calls, 'all selection writes remain discoverable, including consent escalation').toHaveLength(14)
    let routed = 0
    for (const call of calls) {
      if (ts.isCallExpression(call.parent) && call.parent.expression.getText() === 'persistSelection') {
        expect(call.parent.arguments[0]?.getText()).toMatch(/^'this /)
        routed++
        continue
      }
      const chain = callStatement(call)
      if (call.expression.getText() === 'api.setApprovalMode') {
        expect(chain).toContain(".catch(reportActionFailure('apply this approval mode'))")
        expect(chain).toContain('approval: result.mode')
        routed++
      } else {
        const escalation = namedOwner(CODE, 'switchToAgentAndRun')
        expect(escalation).toContain(chain)
        expect(escalation).toMatch(/catch \(e\) \{[\s\S]*notify\([\s\S]*'error'\)[\s\S]*return/)
      }
    }
    expect(routed, '13 ordinary selection writes reach a failure reporter').toBe(13)
    const reporter = readFileSync(join(process.cwd(), 'src/app/shell/reportingWrite.ts'), 'utf8')
    expect(namedOwner(reporter, 'reportActionFailure')).toContain("notify(failureSentence(what, error), 'error')")
    expect(nodes(CODE, node => ts.isCallExpression(node) && node.expression.getText() === 'persistSelection')).toHaveLength(12)
  })

  it('each report names WHICH pick failed', () => {
    for (const what of ['this agent', 'this model', 'this approval mode', 'this task mode', 'this reasoning effort', 'this natural-voice setting'])
      expect(CODE, `missing a report for ${what}`).toContain(`persistSelection('${what}'`)
  })

  it('the composer still updates OPTIMISTICALLY — the fix reports, it does not block', () => {
    expect(CODE).toMatch(/const nextSel = \{ \.\.\.selection, \.\.\.patch \}\s*\n\s*setSelection\(nextSel\)/)
  })

  it('and it does NOT revert the selection — the deliberate non-fix', () => {
    const at = CODE.indexOf('const persistSelection')
    const body = CODE.slice(at, at + 320)
    expect(body, 'the reporter must not roll local state back').not.toMatch(/setSelection\(/)
  })

  it('the consent-gated escalation reports AND stops, rather than sending anyway', () => {
    const at = CODE.indexOf('async function switchToAgentAndRun')
    const body = CODE.slice(at, at + 700)
    expect(body, 'the flip must be captured').toMatch(/try \{ await api\.setTaskMode\('agent', s\) \}/)
    expect(body, 'it must report').toMatch(/notify\(`Couldn't switch this session to Agent/)
    expect(body, 'and it must NOT fall through to the send').toMatch(/return[\s\S]{0,12}\}[\s\S]{0,12}\}/)
    const sendAt = body.indexOf('await send(text)')
    const returnAt = body.indexOf('return')
    expect(returnAt, 'the early return precedes the send').toBeLessThan(sendAt)
  })

  it('notify is the file’s own idiom, not a new import — the convergence claim', () => {
    expect(SRC).toMatch(/import \{ notify \} from '\.\.\/app\/shell\/appSdk'/)
    const uses = (CODE.match(/notify\(/g) || []).length
    expect(uses, 'ChatPage already reported failures this way').toBeGreaterThan(5)
  })
})
