import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const strip = (s: string) => s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')

describe('a clipped tool identifier can still be read', () => {
  const PAGE = strip(read('features/tools/ToolsPage.tsx'))

  it('the tool name carries its own value as a title', () => {
    expect(PAGE).toMatch(/className="truncate font-mono text-on-surface text-\[0\.8125rem\]" title=\{t\.name\}>\{t\.name\}<\/span>/)
  })

  it('the server name does too', () => {
    expect(PAGE).toMatch(/className="truncate font-mono text-on-surface text-\[0\.8125rem\]" title=\{s\.name\}>\{s\.name\}<\/span>/)
  })

  it('and the server address line, which is the most tail-heavy string here', () => {
    expect(PAGE).toMatch(/title=\{s\.url \|\| \[s\.command, \.\.\.\(s\.args \?\? \[\]\)\]\.join\(' '\)\}>\{s\.url \|\| \[s\.command, \.\.\.\(s\.args \?\? \[\]\)\]\.join\(' '\)\}<\/p>/)
  })

  it('every title is the rendered expression, not a paraphrase', () => {
    for (const [what, expr] of [['tool', 't.name'], ['server', 's.name']] as const) {
      const e = expr.replace('.', '\\.')
      expect(new RegExp(`title=\\{${e}\\}>\\{${e}\\}<`).test(PAGE), `${what}`).toBe(true)
    }
    const addr = /title=\{(s\.url \|\| \[s\.command[^}]*)\}>\{(s\.url \|\| \[s\.command[^}]*)\}</.exec(PAGE)
    expect(addr, 'address title and text are the same expression').toBeTruthy()
    expect(addr?.[1]).toBe(addr?.[2])
  })

  it('all three still truncate — the fix is recovery, not re-layout', () => {
    expect(PAGE).toMatch(/className="truncate font-mono text-on-surface text-\[0\.8125rem\]"/)
    expect(PAGE).toMatch(/className="mt-0\.5 truncate font-mono text-on-surface-low text-\[0\.75rem\]"/)
  })

  it('the mono type survives — it is what makes these read as identifiers', () => {
    expect((PAGE.match(/truncate font-mono/g) || []).length, 'monospace truncating identifiers').toBeGreaterThanOrEqual(3)
  })
})
