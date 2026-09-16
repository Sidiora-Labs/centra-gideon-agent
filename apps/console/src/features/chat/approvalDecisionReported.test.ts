import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src/features/ChatPage.tsx")
const raw = readFileSync(SRC, 'utf8')
const scan = raw.replace(/=>/g, '⇒')

function approveBody(): string {
  const at = raw.indexOf('const approve = useCallback(')
  expect(at, 'the approve callback must exist').toBeGreaterThan(-1)
  const end = raw.indexOf('}, [])', at)
  expect(end, 'the callback must be closed').toBeGreaterThan(at)
  return raw.slice(at, end + 6)
}

describe('a permission decision that fails says so, and moves nothing', () => {
  it('the decision reports its failure with the server’s message', () => {
    expect(approveBody()).toContain(".catch(reportActionFailure('record your decision'))")
  })

  it('the write no longer swallows', () => {
    const body = approveBody().replace(/=>/g, '⇒')
    expect(body, 'a swallowed permission decision tells the user nothing').not.toMatch(
      /api\.approve\([^)]*\)\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/,
    )
  })

  it('the posture pill only moves after the write LANDS', () => {
    const body = approveBody()
    const then = body.indexOf('.then(')
    const mirror = body.indexOf('setSelection((sel)')
    expect(then, 'the write must have a success continuation').toBeGreaterThan(-1)
    expect(mirror, 'the mirror must exist').toBeGreaterThan(-1)
    expect(mirror, 'the mirror must sit INSIDE .then(), not run unconditionally').toBeGreaterThan(then)
    expect(body.indexOf('.catch('), 'and the failure path comes after it').toBeGreaterThan(mirror)
  })

  it('the raised posture is still derived from the action, not invented', () => {
    const body = approveBody()
    for (const pair of [
      "action === 'trust' || action === 'trust_agent' ? 'trust'",
      "action === 'trust_reads' ? 'trust_reads'",
      "action === 'yolo' ? 'yolo'",
    ]) {
      expect(body, `the ${pair} mapping must survive`).toContain(pair)
    }
    expect(body, 'a single-shot decision raises nothing').toMatch(/:\s*null\b/)
  })

  it('the card is left to the backend — it is not cleared optimistically here', () => {
    const body = approveBody()
    for (const forbidden of ['setTurns(', 'resolved:', 'setSegments(']) {
      expect(body, `approve must not touch transcript state — found ${forbidden}`).not.toContain(
        forbidden,
      )
    }
    const card = readFileSync(join(process.cwd(), "src/features/chat/ApprovalCard.tsx"), 'utf8')
    expect(card, 'the card renders the backend’s outcome').toContain('if (seg.resolved) {')
  })

  it('no OTHER api.approve call in the file swallows — the ratchet', () => {
    const offenders: string[] = []
    for (const m of scan.matchAll(/api\.approve\(/g)) {
      const chain = scan.slice(m.index!, m.index! + 200)
      if (/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/.test(chain)) {
        offenders.push(`line ${scan.slice(0, m.index).split('\n').length}`)
      }
    }
    expect(offenders).toEqual([])
    expect([...scan.matchAll(/api\.approve\(/g)].length).toBeGreaterThanOrEqual(1)
  })
})
