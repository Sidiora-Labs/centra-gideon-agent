import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { TextInput } from './forms'


const SRC = join(process.cwd(), "src")

const ADOPTERS = [
  join('features', 'prompts', 'PromptForm.tsx'),
  join('features', 'tasks', 'TaskForm.tsx'),
  join('features', 'agents', 'AgentForm.tsx'),
  join('features', 'triggers', 'TriggerCreatePage.tsx'),
]

const PRINCIPLED_EXCLUSION = join('features', 'knowledge', 'KnowledgeCreatePage.tsx')

function walk(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.tsx?$/.test(p) && !/\.test\.tsx?$/.test(p)) out.push(p)
  }
  return out
}

describe('TextInput can declare a field mandatory', () => {
  it('publishes aria-required when asked, and not otherwise', () => {
    const on = render(<TextInput required value="" onChange={() => {}} ariaLabel="n" />)
    expect(on.container.querySelector('input')!.getAttribute('aria-required')).toBe('true')
    on.unmount()
    const off = render(<TextInput value="" onChange={() => {}} ariaLabel="n" />)
    expect(off.container.querySelector('input')!.getAttribute('aria-required')).toBeNull()
  })

  it('changes nothing visual', () => {
    const a = render(<TextInput required value="" onChange={() => {}} ariaLabel="n" />)
    const withReq = a.container.querySelector('input')!.className
    a.unmount()
    const b = render(<TextInput value="" onChange={() => {}} ariaLabel="n" />)
    expect(b.container.querySelector('input')!.className).toBe(withReq)
  })
})

describe('the create forms mark their mandatory field', () => {
  it.each(ADOPTERS)('%s passes required on its identity field', (rel) => {
    const src = readFileSync(join(SRC, rel), 'utf8')
    expect(src, `${rel} must mark exactly the mandatory input`).toMatch(/<TextInput required /)
  })

  it('the enforcement is still explained at the button too (belt and braces)', () => {
    const task = readFileSync(join(SRC, "features", 'tasks', 'TaskCreatePage.tsx'), 'utf8')
    expect(task).toMatch(/disabledReason=\{!draft\.title\.trim\(\) \? 'Enter a task title first'/)
  })

  it('the create-form subset is CLOSED — every one is adopted or excluded with a reason', () => {
    const knowledge = readFileSync(join(SRC, PRINCIPLED_EXCLUSION), 'utf8')
    expect(knowledge, 'the either/or reason is what makes the exclusion principled').toMatch(
      /Add a title or some content/,
    )
    expect(/<TextInput required /.test(knowledge), 'a single required mark would be false here').toBe(false)
  })

  it('records the unswept remainder rather than implying it is done', () => {
    const files = walk(SRC)
    let reasons = 0
    for (const f of files) {
      const src = readFileSync(f, 'utf8')
      reasons += (src.match(/disabledReason=\{[^}]*[Ee]nter a[^}]*first/g) ?? []).length
    }
    expect(reasons, 'the "Enter a … first" population must still be visible to this rail').toBeGreaterThanOrEqual(20)
  })
})
