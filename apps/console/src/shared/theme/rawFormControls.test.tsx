import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Field, TextArea, TextInput } from '../ui/forms'


const SRC = join(process.cwd(), "src")

function accessibleName(el: Element, root: HTMLElement): string | null {
  const by = el.getAttribute('aria-labelledby')
  if (by) return root.ownerDocument.body.querySelector(`[id="${CSS.escape(by)}"]`)?.textContent?.trim() ?? '(dangling id)'
  return el.getAttribute('aria-label')
}

describe('a form primitive inside a Field claims its label; a raw element cannot', () => {
  it('TextInput in a Field is named', () => {
    const { container } = render(
      <Field label="Name"><TextInput value="" onChange={() => {}} placeholder="Project name…" /></Field>,
    )
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement)).toBe('Name')
  })

  it('TextArea in a Field is named', () => {
    const { container } = render(
      <Field label="Brief"><TextArea value="" onChange={() => {}} rows={4} /></Field>,
    )
    expect(accessibleName(container.querySelector('textarea')!, container as HTMLElement)).toBe('Brief')
  })

  it('a RAW input in the very same Field is NOT named — the defect, reproduced', () => {
    const { container } = render(
      <Field label="Name"><input placeholder="Project name…" /></Field>,
    )
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement)).toBeNull()
  })

  it('a raw element with its own aria-label IS named — the escape hatch', () => {
    const { container } = render(<input placeholder="file name" aria-label="New file name" />)
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement)).toBe('New file name')
  })
})

describe('the three fixed surfaces', () => {
  it('ProjectsSection keeps its own Field layout but PUBLISHES its label id', () => {
    const src = readFileSync(join(SRC, 'features/projects/ProjectsSection.tsx'), 'utf8')
    expect(/function Field\b/.test(src), 'the local Field is a kept layout, not drift').toBe(true)
    expect(src).toMatch(/import \{[^}]*\bFieldLabelProvider\b[^}]*\} from '\.\.\/\.\.\/shared\/ui\/forms'/)
    expect(src).toMatch(/<FieldLabelProvider value=\{labelId\}>/)
    expect(src).toMatch(/<span id=\{labelId\}/)
    expect(src).toMatch(/<TextInput autoFocus value=\{name\}/)
    expect(src).toMatch(/<TextArea value=\{brief\}/)
  })

  it('ToolsPage env field is a TextArea, and the hand-copied class is gone', () => {
    const src = readFileSync(join(SRC, 'features/tools/ToolsPage.tsx'), 'utf8')
    expect(src).toMatch(/<TextArea value=\{env\}/)
    expect(/const mcpInputCls =/.test(src), 'mcpInputCls should be deleted with its last consumer').toBe(false)
  })

  it('the Files inline create input names itself, and tracks the mode', () => {
    const src = readFileSync(join(SRC, 'features/files/FilesSection.tsx'), 'utf8')
    expect(src).toMatch(/aria-label=\{creating === 'dir' \? 'New folder name' : 'New file name'\}/)
  })
})
