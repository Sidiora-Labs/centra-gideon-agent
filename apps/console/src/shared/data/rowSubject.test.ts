import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { rowSubject } from './rowSubject'



describe('rowSubject joins the parts that identify a row', () => {
  it('joins with an em dash', () => {
    expect(rowSubject(['Loop progress', 'cycle 4 finished'])).toBe('Loop progress — cycle 4 finished')
  })

  it('drops empty, null and undefined parts', () => {
    expect(rowSubject(['Only this', '', null, undefined])).toBe('Only this')
    expect(rowSubject([null, 'Second'])).toBe('Second')
  })

  it('does not say the same thing twice', () => {
    expect(rowSubject(['Refine a skill', 'Refine a skill'])).toBe('Refine a skill')
    expect(rowSubject(['Refine a skill', 'Refine a skill loop-worker — add a step'])).toBe('Refine a skill')
  })

  it('caps at 55 characters with an ellipsis', () => {
    const long = rowSubject(['skills', 'Refine a skill loop-worker — When producing a quorum/roster planning artifact'])
    expect(long.length).toBe(55)
    expect(long.endsWith('…')).toBe(true)
  })

  it('leaves a short subject exactly as it is', () => {
    expect(rowSubject(['triage'])).toBe('triage')
    expect(rowSubject(['a'.repeat(55)]).length).toBe(55)
    expect(rowSubject(['a'.repeat(56)]).length).toBe(55)
  })

  it('takes a cap override for a caller with a different budget', () => {
    expect(rowSubject(['abcdefghij'], 5)).toBe('abcd…')
  })
})

describe('the inbox row names itself by identity, not by kind', () => {
  const SRC2 = join(process.cwd(), "src")
  const code = readFileSync(join(SRC2, 'features/inbox/InboxPage.tsx'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('composes through the shared helper', () => {
    expect(code).toMatch(/label=\{rowSubject\(\[channelBacked \? \(it\.sender_name \|\| it\.sender_id \|\| 'Unknown'\) : km\.label,/)
  })

  it('collapses the message whitespace rather than taking its first line', () => {
    expect(code).toMatch(/previewText\(it\.message\)\]\)/)
    expect(code, 'firstLine is the wrong transform for this data').not.toMatch(/firstLine\(it\.message/)
  })

  it('the kind label is still the FIRST part, so the row still says what sort of thing it is', () => {
    expect(code).toMatch(/: km\.label, previewText\(it\.message/)
  })
})

describe('both composing surfaces use the one helper', () => {
  const SRC = join(process.cwd(), "src")
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
  const codeOf = (rel: string) => read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it("the dashboard's action centre composes through it", () => {
    const code = codeOf('features/dashboard/widgets/ActionCenter.tsx')
    expect(code).toMatch(/const subject = rowSubject\(\[e\.title, e\.sub\]\)/)
    expect(code, 'the hand-rolled join must be gone').not.toMatch(/e\.sub \? `\$\{e\.title\} — \$\{e\.sub\}`/)
  })

  it('the notification rows compose through it', () => {
    const code = codeOf('features/notifications/NotificationsPage.tsx')
    expect(code).toMatch(/rowSubject\(\[n\.title, firstLine\(n\.body \?\? ''\)\]\)/)
    expect(code, 'the local copy of the rule must be gone').not.toMatch(/function rowName/)
    expect(code, 'and its number with it').not.toMatch(/full\.length > 55/)
  })

  it('nothing else re-implements the cap', () => {
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
      })
    const offenders = walk(join(SRC, "features")).filter((abs) => /slice\(0, 5[0-9]\)…|length > 5[0-9] \?/.test(readFileSync(abs, 'utf8')))
    expect(offenders).toEqual([])
  })
})
