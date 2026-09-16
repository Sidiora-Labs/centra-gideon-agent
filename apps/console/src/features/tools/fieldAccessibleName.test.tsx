import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { Field, TextInput } from '../../shared/ui/forms'

vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))


const TOOLS_PAGE = join(process.cwd(), "src/features/tools/ToolsPage.tsx")
const SRC = join(process.cwd(), "src")

function byId(root: HTMLElement, id: string): Element | null {
  return root.ownerDocument.body.querySelector(`[id="${CSS.escape(id)}"]`)
}

function accessibleName(el: Element, root: HTMLElement): string | null {
  const by = el.getAttribute('aria-labelledby')
  if (by) return byId(root, by)?.textContent?.trim() ?? '(dangling id)'
  return el.getAttribute('aria-label')
}

describe('a control inside the shared Field claims its label', () => {
  it('resolves the Field label as its accessible name', () => {
    const { container } = render(
      <Field label="Command" hint="The executable that starts the server.">
        <TextInput value="" onChange={() => {}} placeholder="npx" />
      </Field>,
    )
    const input = container.querySelector('input')!
    expect(accessibleName(input, container as HTMLElement)).toBe('Command')
  })

  it('the aria-labelledby target actually exists', () => {
    const { container } = render(
      <Field label="Endpoint URL"><TextInput value="" onChange={() => {}} /></Field>,
    )
    const by = container.querySelector('input')!.getAttribute('aria-labelledby')
    expect(by).toBeTruthy()
    expect(byId(container as HTMLElement, by!)?.textContent).toBe('Endpoint URL')
  })

  it('a BARE label div strips the name — the shape that was in ToolsPage', () => {
    const { container } = render(
      <div>
        <div>Command</div>
        <TextInput value="" onChange={() => {}} placeholder="npx" />
      </div>,
    )
    const input = container.querySelector('input')!
    expect(accessibleName(input, container as HTMLElement)).toBeNull()
  })

  it('an explicit ariaLabel still wins outside a Field', () => {
    const { container } = render(<TextInput value="" onChange={() => {}} ariaLabel="Search tools" />)
    expect(container.querySelector('input')!.getAttribute('aria-label')).toBe('Search tools')
  })
})

describe('ToolsPage uses the shared Field', () => {
  const src = readFileSync(TOOLS_PAGE, 'utf8')

  it('declares no local Field', () => {
    expect(/function Field\b/.test(src), 'ToolsPage should not declare its own Field').toBe(false)
  })

  it('imports Field from the form family', () => {
    expect(src).toMatch(/import \{[^}]*\bField\b[^}]*\} from '\.\.\/\.\.\/shared\/ui\/forms'/)
  })

  it('still wraps its inputs in Field, so the labels are published', () => {
    expect((src.match(/<Field label=/g) ?? []).length).toBe(8)
  })
})

describe('no page reimplements Field around a form-family control', () => {
  it('a local Field either publishes a label id or holds no form-family control', () => {
    // exported: a local Field is no longer necessarily context-less, so DECLARING one is not the
    const offenders: string[] = []
    const walk = (dir: string): string[] => {
      const out: string[] = []
      for (const n of readdirSync(dir)) {
        const p = join(dir, n)
        if (statSync(p).isDirectory()) { out.push(...walk(p)); continue }
        if (/\.tsx$/.test(n) && !/\.test\.tsx$/.test(n)) out.push(p)
      }
      return out
    }
    for (const abs of walk(join(SRC, "features"))) {
      const src = readFileSync(abs, 'utf8')
      if (!/function Field\b/.test(src)) continue
      if (/<FieldLabelProvider\b/.test(src)) continue
      if (/<(TextInput|TextArea|Select)\b/.test(src)) offenders.push(abs.slice(SRC.length + 1))
    }
    expect(
      offenders,
      `a local Field that publishes NO label id, wrapping a form-family control (the control loses ` +
        `its accessible name — either publish via FieldLabelProvider or use ui/forms' Field):\n  ` +
        offenders.join('\n  '),
    ).toEqual([])
  })
})
