import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('a scrollable region with no focusable content owns a tab stop', () => {
  const REGIONS: Array<[string, RegExp, string]> = [
    ['features/settings/SecurityPanel.tsx', /max-h-44 overflow-y-auto/, 'built-in shell denylist'],
    ['features/inbox/InboxDetail.tsx', /max-h-64 overflow-auto/, 'inbox procedure'],
  ]

  for (const [file, marker, what] of REGIONS) {
    it(`${what} is keyboard-scrollable and named`, () => {
      const src = read(file)
      const at = src.search(marker)
      expect(at, `the ${what} scroll container moved — re-measure before editing this rail`).toBeGreaterThan(-1)
      const tag = src.slice(at, src.indexOf('>', at))
      expect(tag, 'needs a tab stop, or the region cannot be scrolled by keyboard').toContain('tabIndex={0}')
      expect(tag, 'role=group keeps it announced as a container, not an unnamed widget').toContain('role="group"')
      expect(tag, 'an unnamed region announces nothing useful').toMatch(/aria-label=/)
    })
  }
})

describe('the insights dock header is two siblings, not nested controls', () => {
  const src = read('features/knowledge/KnowledgeDetail.tsx')

  it('has no role=button span left in the dock header', () => {
    const header = src.slice(src.indexOf('group/dock'), src.indexOf('{open && ('))
    expect(header, 'a role=button inside the disclosure is the nested-interactive shape')
      .not.toMatch(/role="button"/)
  })

  it('the disclosure announces its state', () => {
    expect(src).toMatch(/aria-expanded=\{hasMore \? open : undefined\}/)
  })

  it('the chevron is decorative, not a duplicate tab stop', () => {
    const chevronBlock = src.slice(src.indexOf('group-hover/dock:bg-surface-high') - 240,
      src.indexOf('group-hover/dock:bg-surface-high') + 60)
    expect(chevronBlock).toContain('aria-hidden')
    expect(chevronBlock, 'a <button> here would duplicate the disclosure').not.toMatch(/<button/)
  })

  it('Regenerate uses the shared Button primitive', () => {
    expect(src).toMatch(/<Button variant="ghost" size="sm" onClick=\{onGenerate\}/)
    expect(src).toMatch(/^import \{ Button \} from '\.\.\/\.\.\/shared\/ui\/Button'$/m)
  })
})

describe('an icon-only button carries its own name', () => {
  it('the audit refresh button is named', () => {
    const src = read('features/settings/AuditPanel.tsx')
    const line = src.split('\n').find((l) => l.includes('onClick={reload}') && l.includes('<Button'))
    expect(line, 'the audit refresh Button moved — re-measure').toBeTruthy()
    expect(line!, 'an icon-only Button with no title/aria-label has no accessible name').toMatch(/title=/)
  })
})
