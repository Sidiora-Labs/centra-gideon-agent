import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TextLink } from './TextLink'


describe('TextLink is a 24px target wherever it sits', () => {
  it('carries vertical padding, not a display change', () => {
    render(<TextLink onClick={vi.fn()}>Personal</TextLink>)
    const a = screen.getByText('Personal')
    expect(a.className).toMatch(/\bpy-1\b/)
    expect(a.className, 'a min-height would need inline-flex, which moves the baseline').not.toMatch(/min-h-6/)
  })

  it('hands the space back so the line rhythm is unchanged', () => {
    render(<TextLink onClick={vi.fn()}>Personal</TextLink>)
    expect(screen.getByText('Personal').className).toMatch(/-my-1/)
  })

  it('still only goes inline-flex when it has an icon to align', () => {
    const { container } = render(<TextLink onClick={vi.fn()}>plain</TextLink>)
    expect(container.firstElementChild!.className).not.toMatch(/inline-flex/)
  })
})

describe("the task row's checkbox paints 20px and clicks 24px", () => {
  const src = readFileSync(join(process.cwd(), "src/features/tasks/TasksListPage.tsx"), 'utf8')

  it('is a transparent 24px button around the painted control', () => {
    expect(src).toMatch(/className="shrink-0 grid size-6 -m-0\.5 place-items-center"/)
  })

  it('keeps the 20px painted box, now as a child span', () => {
    expect(src).toMatch(/<span className=\{`grid size-5 place-items-center rounded-md border/)
  })

  it('returns the 4px so no row reflows', () => {
    expect(src).toMatch(/size-6 -m-0\.5/)
  })
})

describe('the detail routes added after the first census', () => {
  const SRC = join(process.cwd(), "src")
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  it("the project header's Rename button carries a 24px hit box", () => {
    expect(read('features/projects/ProjectsSection.tsx'))
      .toMatch(/IconButton icon=\{Pencil\} label="Rename" size=\{24\}/)
  })

  it("the context/workspace row's Open-in-Files button carries one too", () => {
    expect(read('features/projects/ProjectsSection.tsx'))
      .toMatch(/IconButton icon=\{FolderOpen\} label=\{`Open \$\{label\} in Files`\}[\s\S]{0,40}?size=\{24\}/)
  })

  it('neither reintroduces the bare padding that made them undersized', () => {
    const src = read('features/projects/ProjectsSection.tsx')
    expect(src, 'the 21x21 Rename shape').not.toMatch(/aria-label="Rename"[\s\S]{0,120}?rounded-md p-1 /)
    expect(src, 'the 16x16 Open-in-Files shape').not.toMatch(/in Files`\}[\s\S]{0,200}?rounded p-0\.5 /)
  })

  it('BoardCollapse is deliberately still padding-based', () => {
    expect(read('shared/ui/BoardCollapse.tsx'), 'if this changes, re-measure the board row height first')
      .toMatch(/rounded-md p-1 /)
  })

  it('and it reaches 24px through the overlay idiom instead of a margin', () => {
    const src = read('shared/ui/BoardCollapse.tsx')
    expect(src, 'the collapse button should carry .hit-24').toMatch(/\bhit-24\b/)
    expect(src, 'a negative margin here reflows the whole board — that is the measured trade-off')
      .not.toMatch(/-m[xy]?-/)
  })
})

describe('.hit-24 expands the pointer target without touching layout', () => {
  const tokens = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
  const rule = tokens.slice(tokens.indexOf('.hit-24 {'), tokens.indexOf('}', tokens.indexOf('.hit-24::before')) + 1)

  it('the utility exists and is not vacuous', () => {
    expect(tokens, '.hit-24 is not defined').toMatch(/^\.hit-24 \{/m)
    expect(tokens, '.hit-24::before is not defined').toMatch(/^\.hit-24::before \{/m)
    expect(rule.length, 'the slice found no rule body').toBeGreaterThan(80)
  })

  it('the host is positioned, or an absolute ::before escapes to the wrong ancestor', () => {
    expect(rule).toMatch(/\.hit-24 \{[^}]*position:\s*relative/s)
  })

  it('the inset is DERIVED from the floor, never a hand-tuned pixel', () => {
    expect(rule, 'the floor must be a variable so a call site can state its own drawn size')
      .toMatch(/--hit-min:\s*24px/)
    expect(rule).toMatch(/--hit-size:/)
    expect(rule, 'inset must compute from --hit-min and --hit-size').toMatch(/inset:\s*calc\([^)]*var\(--hit-min\)/)
    expect(rule, 'the shortfall must be halved — a full inset overshoots by 2x').toMatch(/\/\s*2\s*\)/)
    expect(rule, 'clamp at 0 so a control already at the floor does not shrink').toMatch(/max\(0px,/)
  })

  it('the ::before paints nothing and forwards its events', () => {
    expect(rule, 'content is required or the pseudo-element does not generate a box').toMatch(/content:\s*""/)
    expect(rule).toMatch(/position:\s*absolute/)
    expect(rule, 'the overlay must accept pointer events on its host\'s behalf').not.toMatch(/pointer-events:\s*none/)
  })

  it('it states what it cannot do, so the caveat is not rediscovered', () => {
    expect(tokens, 'the axe caveat must be recorded beside the utility').toMatch(/axe[\s\S]{0,400}does not change/)
  })
})
