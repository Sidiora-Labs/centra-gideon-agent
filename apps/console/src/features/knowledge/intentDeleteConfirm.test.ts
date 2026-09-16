import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = readFileSync(join(process.cwd(), "src/features/knowledge/KnowledgeListPage.tsx"), 'utf8')
const CODE = SRC.replace(/\/\*\*[\s\S]*?\*\//g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

describe('deleting an intent is confirmed from both places', () => {
  it('no delete call remains unguarded', () => {
    const calls = [...CODE.matchAll(/api\.deleteKnowledgeIntent\(/g)]
    expect(calls.length, 'the row control and the detail control').toBe(2)
    for (const m of calls) {
      const before = CODE.slice(Math.max(0, m.index - 260), m.index)
      expect(before, 'a confirm precedes this delete').toMatch(/confirmIntentDelete\([^)]*\)\)\) return/)
    }
  })

  it('goes through the app-wide helper, not a bespoke dialog', () => {
    expect(SRC).toMatch(/import \{ confirm, confirmDelete, promptInput \} from '\.\.\/\.\.\/shared\/ui\/dialog'/)
    expect(CODE).toMatch(/confirmDelete\('intent', rowSubject\(\[goal\], 40\)/)
  })

  it('the body states the real consequence, and counts it', () => {
    expect(CODE).toMatch(/Everything it gathered goes with it/)
    expect(CODE, 'the count is interpolated, not hard-coded').toMatch(/\$\{gathered\}/)
    expect(CODE, 'singular and plural').toMatch(/gathered === 1 \? 'match' : 'matches'/)
    expect(CODE, 'and it does not overstate an empty intent')
      .toMatch(/gathered nothing yet, so only the intent itself goes/)
  })

  it('the name is capped, so a sentence-long goal cannot become the dialog title', () => {
    expect(CODE).toMatch(/rowSubject\(\[goal\], 40\)/)
  })

  it('the shelf precedent it mirrors still ships — the vacuity floor', () => {
    expect(CODE, 'the shelf confirmation is still there').toMatch(/The shelf goes away\. The items on it stay/)
    expect(CODE, 'and still through a real dialog').toMatch(/const ok = await confirm\(\{/)
  })

  it('both controls keep the names and stop-propagation they already had', () => {
    expect(CODE).toMatch(/ariaLabel=\{`Delete intent: \$\{rowSubject\(\[it\.goal \|\| it\.id\], 40\)\}`\}/)
    const spans = [...CODE.matchAll(/<span onClick=\{\(e\) => e\.stopPropagation\(\)\}>/g)]
    expect(spans.length, 'row and detail both still swallow the click').toBeGreaterThanOrEqual(2)
  })
})
