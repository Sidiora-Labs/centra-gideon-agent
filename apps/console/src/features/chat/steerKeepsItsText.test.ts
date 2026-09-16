import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const CHAT = readFileSync(
  join(process.cwd(), "src/features/ChatPage.tsx"),
  'utf8',
)

const strip = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

function steerBranch(): string {
  const body = strip(CHAT)
  const at = body.indexOf('if (isStreaming) {')
  expect(at, 'the steer branch must be found before it can be measured').toBeGreaterThan(-1)
  const end = body.indexOf('return', at)
  expect(end).toBeGreaterThan(at)
  return body.slice(at, end)
}

describe('a failed mid-stream steer keeps the text the user typed', () => {
  it('the draft is NOT cleared before the request goes out', () => {
    const fn = steerBranch()
    const send = fn.indexOf('api.sendChat')
    const clear = fn.indexOf('setInput(')
    expect(send, 'the steer send must be in this branch').toBeGreaterThan(-1)
    expect(clear, 'and the branch must still clear the draft somewhere').toBeGreaterThan(-1)
    expect(clear, 'clearing before the send is what destroyed the text').toBeGreaterThan(send)
  })

  it('the clear is guarded, so a composer the user re-typed into is not wiped', () => {
    const fn = steerBranch()
    expect(fn, 'clear only if the composer still holds exactly what was sent')
      .toMatch(/setInput\(\(cur\) => \(cur === t \? '' : cur\)\)/)
  })

  it('the rejection is reported instead of swallowed', () => {
    const fn = steerBranch()
    expect(fn, 'an empty catch is the defect').not.toMatch(/\.catch\(\(\) => \{\s*\}\)/)
    expect(fn, "and it uses this file's own convention for a user-initiated write")
      .toMatch(/\.catch\(reportActionFailure\(/)
  })

  it('the steered chip still only appears when the server said it steered', () => {
    const fn = steerBranch()
    expect(fn).toMatch(/if \(r\?\.steered\) setSteered\(/)
  })

  it('the reporter is imported, so the catch is a real call and not a stray identifier', () => {
    expect(strip(CHAT)).toMatch(/import \{[^}]*\breportActionFailure\b[^}]*\} from '\.\.\/app\/shell\/reportingWrite'/)
  })
})

describe("the file no longer claims a safety it does not have", () => {
  it('the "nothing is dropped" sentence is scoped to the two success outcomes', () => {
    expect(CHAT, 'the unqualified claim must not return')
      .not.toMatch(/Either way nothing is dropped/)
    expect(CHAT, 'and the rejection outcome must be named where the claim used to sit')
      .toMatch(/THIRD OUTCOME/)
  })
})
