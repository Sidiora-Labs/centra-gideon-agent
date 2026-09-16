import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { VariableRow } from './VariableRow'


const PROMPTS = join(process.cwd(), "src/features/prompts")

const code = (f: string) =>
  readFileSync(join(PROMPTS, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const nameOf = (el: Element) => el.getAttribute('aria-label')

describe('VariableRow names each row after the variable it edits', () => {
  it('two rows with different variables get different names', () => {
    const { container } = render(
      <>
        <VariableRow v={{ name: 'city', type: 'text' }} rowIndex={0} onChange={() => {}} onRemove={() => {}} />
        <VariableRow v={{ name: 'tone', type: 'text' }} rowIndex={1} onChange={() => {}} onRemove={() => {}} />
      </>,
    )
    const names = [...container.querySelectorAll('input, select')].map(nameOf)
    expect(names).toContain('Name of variable "city"')
    expect(names).toContain('Name of variable "tone"')
    expect(new Set(names).size, `duplicate names: ${names.join(' / ')}`).toBe(names.length)
  })

  it('two BLANK rows still differ — the rowIndex fallback', () => {
    const { container } = render(
      <>
        <VariableRow v={{ name: '', type: 'text' }} rowIndex={0} onChange={() => {}} onRemove={() => {}} />
        <VariableRow v={{ name: '', type: 'text' }} rowIndex={1} onChange={() => {}} onRemove={() => {}} />
      </>,
    )
    const names = [...container.querySelectorAll('input, select')].map(nameOf)
    expect(names).toContain('Name of variable row 1')
    expect(names).toContain('Name of variable row 2')
    expect(new Set(names).size).toBe(names.length)
  })

  it('the Remove button and the choices field are scoped too', () => {
    const { container } = render(
      <VariableRow v={{ name: 'mode', type: 'select', options: ['a', 'b'] }} rowIndex={0} onChange={() => {}} onRemove={() => {}} />,
    )
    const all = [...container.querySelectorAll('input, select, button')].map(nameOf).filter(Boolean)
    expect(all).toContain('Remove variable "mode"')
    expect(all).toContain('Choices for variable "mode"')
  })

  it('a constant name would regress this — pinned at the source', () => {
    const src = code('VariableRow.tsx')
    for (const re of [
      /aria-label=\{`Name of variable \$\{which\}`\}/,
      /aria-label=\{`Type of variable \$\{which\}`\}/,
      /aria-label=\{`Description of variable \$\{which\}`\}/,
      /aria-label=\{`Default value of variable \$\{which\}`\}/,
    ]) expect(src).toMatch(re)
    expect(/aria-label="Variable (name|type|description|default value|choices)"/.test(src)).toBe(false)
  })

  it('every call site passes rowIndex, or blank rows collide again', () => {
    for (const f of ['PromptEditFields.tsx', 'PromptForm.tsx', 'SnippetForm.tsx']) {
      const tag = code(f).match(/<VariableRow\b[\s\S]*?\/>/)
      expect(tag, `${f} should mount VariableRow`).toBeTruthy()
      expect(tag![0], `${f} must pass rowIndex`).toMatch(/rowIndex=\{i\}/)
    }
  })
})

describe('the controls that had no name at all', () => {
  it('PromptEditFields names all four (its Section is an injected prop that publishes nothing)', () => {
    const src = code('PromptEditFields.tsx')
    for (const n of ['Prompt title', 'Prompt description', 'Prompt template']) {
      expect(src).toContain(`aria-label="${n}"`)
    }
    expect(src).toContain(`ariaLabel="Prompt tags"`)
  })

  it('PromptDetail Try-it inputs name themselves — the id was DANGLING', () => {
    const src = code('PromptDetail.tsx')
    expect(src).toMatch(/const label = `\$\{v\.name\} value`/)
    expect((src.match(/aria-label=\{label\}/g) ?? []).length).toBe(4)
    expect(/htmlFor=\{fid\}/.test(src), 'no label[for] exists — do not claim the id names it').toBe(false)
  })

  it('SnippetDetail preview inputs match PromptDetail wording', () => {
    expect(code('SnippetDetail.tsx')).toMatch(/aria-label=\{`\$\{v\.name\} value`\}/)
  })

  it('SnippetForm body is named, and keeps its ref', () => {
    const src = code('SnippetForm.tsx')
    expect(src).toContain('aria-label="Snippet content"')
    expect(src).toMatch(/<textarea ref=\{taRef\}/)
  })
})

describe('the rail is not vacuously green', () => {
  it('every file this cycle touched exists and is scanned', () => {
    const files = readdirSync(PROMPTS)
    for (const f of ['VariableRow.tsx', 'PromptEditFields.tsx', 'PromptDetail.tsx', 'SnippetDetail.tsx', 'SnippetForm.tsx', 'PromptForm.tsx']) {
      expect(files, `${f} must exist`).toContain(f)
      expect(code(f).length).toBeGreaterThan(200)
    }
  })
})
