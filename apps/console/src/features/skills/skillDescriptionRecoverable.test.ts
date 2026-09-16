import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const strip = (s: string) => s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')

describe('a clipped skill description can still be read', () => {
  const SKILLS = strip(read('features/skills/SkillsPage.tsx'))
  const FORM = strip(read('features/agents/AgentForm.tsx'))

  it('the skills list row hands over the whole description', () => {
    expect(SKILLS).toMatch(/className="mt-0\.5 truncate text-on-surface-low text-\[0\.8125rem\]" title=\{s\.description\}>\{s\.description\}<\/p>/)
  })

  it("the agent form's picker hands over both its texts", () => {
    expect(FORM).toMatch(/className="truncate text-on-surface text-\[0\.8125rem\]" title=\{o\.label\}>\{o\.label\}<\/span>/)
    expect(FORM).toMatch(/className="block truncate text-on-surface-low text-\[0\.75rem\]" title=\{o\.hint\}>\{o\.hint\}<\/span>/)
  })

  it('each title is the rendered value, not a paraphrase', () => {
    for (const [name, src, expr] of [
      ['skills', SKILLS, 's.description'],
      ['form label', FORM, 'o.label'],
      ['form hint', FORM, 'o.hint'],
    ] as const) {
      const re = new RegExp(`title=\\{${expr.replace('.', '\\.')}\\}>\\{${expr.replace('.', '\\.')}\\}<`)
      expect(re.test(src), `${name}: title and text are one expression`).toBe(true)
    }
  })

  it('all three still truncate — the fix is recovery, not re-layout', () => {
    expect(SKILLS).toMatch(/mt-0\.5 truncate text-on-surface-low/)
    expect(FORM).toMatch(/className="truncate text-on-surface text-\[0\.8125rem\]"/)
    expect(FORM).toMatch(/className="block truncate text-on-surface-low/)
  })

  it('the picker is generic, so the fix is not skills-only — the leverage claim', () => {
    expect(FORM).toMatch(/\{o\.hint &&/)
    expect(FORM, 'driven by an options list').toMatch(/options\.map\(|options\.length/)
  })

  it('the row keeps the titles it already had on its status chips', () => {
    expect(SKILLS).toMatch(/title="Always loaded"/)
    expect(SKILLS).toMatch(/title="Integrity check failed — files changed since install"/)
  })
})
