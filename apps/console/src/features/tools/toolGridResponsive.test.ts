import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = readFileSync(join(process.cwd(), "src/features/tools/ToolsPage.tsx"), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

describe('the tool grid gives a name room on a phone', () => {
  it('is one column below sm, two above', () => {
    expect(CODE).toMatch(/<div className="grid grid-cols-1 gap-s sm:grid-cols-2">/)
  })

  it('no unconditional two-column grid remains on this page', () => {
    expect(CODE, 'a bare grid-cols-2 would collapse the name again')
      .not.toMatch(/(?<![:-])\bgrid-cols-2\b/)
    expect(CODE).toMatch(/sm:grid-cols-2/)
  })

  it('the cell still holds everything that made it tight — the vacuity floor', () => {
    expect(CODE, 'the wrench').toMatch(/<Wrench size=\{16\}/)
    expect(CODE, 'the approval shield').toMatch(/t\.requires_approval && <ShieldAlert/)
    expect(CODE, 'the risk badge').toMatch(/<RiskBadge risk=\{t\.risk_level\} \/>/)
    expect(CODE, 'and the disabled pill').toMatch(/off && <span[^>]*>Disabled<\/span>/)
  })

  it('the name is still the truncating identifier it was', () => {
    const span = /<span className="truncate font-mono text-on-surface text-\[0\.8125rem\]"[^>]*>\{t\.name\}<\/span>/.exec(CODE)?.[0] ?? ''
    expect(span, 'the tool name is still a truncating mono span').toBeTruthy()
    expect(span, 'and it hands over its full value').toContain('title={t.name}')
  })

  it('the servers list below is unaffected — it was never a grid', () => {
    expect(CODE).toMatch(/<div className="flex flex-col gap-2">/)
  })
})
