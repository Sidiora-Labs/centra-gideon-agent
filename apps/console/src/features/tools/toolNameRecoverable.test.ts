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

  it('the address title preserves the sanitized display fields and transport', () => {
    expect(PAGE).toContain("title={[s.transport, s.display_url, s.display_command, ...(s.display_args ?? [])].filter(Boolean).join(' ')}")
    expect(PAGE).toContain("{s.transport === 'stdio' ? [s.display_command, ...(s.display_args ?? [])].filter(Boolean).join(' ') : s.display_url}")
    expect(PAGE).not.toMatch(/title=\{s\.url/)
  })

  it('identifier titles retain their exact rendered values', () => {
    for (const [what, expr] of [['tool', 't.name'], ['server', 's.name']] as const) {
      const e = expr.replace('.', '\\.')
      expect(new RegExp(`title=\\{${e}\\}>\\{${e}\\}<`).test(PAGE), `${what}`).toBe(true)
    }
  })

  it('all three still truncate — the fix is recovery, not re-layout', () => {
    expect(PAGE).toMatch(/className="truncate font-mono text-on-surface text-\[0\.8125rem\]"/)
    expect(PAGE).toMatch(/className="mt-0\.5 truncate font-mono text-on-surface-low text-\[0\.75rem\]"/)
  })

  it('the mono type survives — it is what makes these read as identifiers', () => {
    expect((PAGE.match(/truncate font-mono/g) || []).length, 'monospace truncating identifiers').toBeGreaterThanOrEqual(3)
  })
})
