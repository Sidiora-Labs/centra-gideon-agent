import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { Plus } from 'lucide-react'
import { EmptyState, ListRow } from './ListScaffold'


function classOf(el: Element | null): Set<string> {
  return new Set((el?.getAttribute('class') ?? '').trim().split(/\s+/).filter(Boolean))
}
function expectTokens(el: Element | null, tokens: string[]) {
  const have = classOf(el)
  for (const t of tokens) expect(have, `missing "${t}" in: ${[...have].join(' ')}`).toContain(t)
}

describe('EmptyState', () => {
  it('outer column is the shared centered idiom', () => {
    const { container } = render(<EmptyState title="Nothing here" />)
    expectTokens(container.firstElementChild, [
      'flex', 'flex-col', 'items-center', 'gap-l', 'py-2xl', 'text-center',
    ])
  })

  it('no-icon branch renders the Spark mark, not a tinted chip', () => {
    const { container } = render(<EmptyState title="No loops yet" />)
    const mark = container.querySelector('img[alt="Gideon"]')
    expect(mark).not.toBeNull()
    expect(mark?.getAttribute('width')).toBe('36')
    expect(mark?.getAttribute('height')).toBe('36')
    expect(container.querySelector('span.rounded-xl')).toBeNull()
  })

  it('icon branch wraps the glyph in the canonical tinted size-12 chip', () => {
    const { container } = render(<EmptyState icon={Plus} title="No code projects yet" />)
    const chip = container.querySelector('span.rounded-xl')
    expectTokens(chip, ['inline-flex', 'size-12', 'items-center', 'justify-center', 'rounded-xl'])
    expect(chip?.getAttribute('style')).toContain('color-mix')
    expect(classOf(chip?.querySelector('svg') ?? null)).toContain('text-primary')
  })

  it('hint rides the on-ramp type role and the 420px measure', () => {
    const { container } = render(<EmptyState title="t" hint="a subline" />)
    const p = container.querySelector('p')
    expectTokens(p, ['mt-1', 'max-w-[420px]', 'text-on-surface-low'])
    expect(p?.getAttribute('data-type')).toBe('body-m')
    expect(classOf(p)).not.toContain('text-[0.875rem]')
    expect(render(<EmptyState title="t" />).container.querySelector('p')).toBeNull()
  })

  it('title is the headline-s role on the on-surface tone', () => {
    const h2 = render(<EmptyState title="No loops yet" />).container.querySelector('h2')
    expect(h2?.getAttribute('data-type')).toBe('headline-s')
    expect(classOf(h2)).toContain('text-on-surface')
    expect(h2?.textContent).toBe('No loops yet')
  })

  it('CTA is a default-size Button (not sm) and fires onClick', () => {
    const onClick = vi.fn()
    const { container } = render(
      <EmptyState title="t" action={{ label: 'Start a loop', onClick, icon: Plus }} />,
    )
    const btn = container.querySelector('button')
    expectTokens(btn, ['h-10'])
    expect(btn?.textContent).toContain('Start a loop')
    fireEvent.click(btn!)
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(render(<EmptyState title="t" />).container.querySelector('button')).toBeNull()
  })
})


describe('ListRow', () => {
  it('an interactive row exposes ONE button-role tab stop', () => {
    const { getByRole, container } = render(<ListRow onClick={() => {}} label="Row">Row</ListRow>)
    const hit = getByRole('button', { name: 'Row' })
    expect(hit.tagName).toBe('BUTTON')
    expect(hit.getAttribute('tabindex')).toBeNull()
    expect(container.querySelectorAll('[tabindex="0"]').length).toBe(0)
    expect(container.firstElementChild!.getAttribute('tabindex')).toBe('-1')
  })

  it('activates on click — including the synthetic click Enter/Space produce', () => {
    const onClick = vi.fn()
    const { getByRole } = render(<ListRow onClick={onClick} label="Row">Row</ListRow>)
    fireEvent.click(getByRole('button', { name: 'Row' }))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('ignores other keys so list-level shortcuts still pass through', () => {
    const onClick = vi.fn()
    const { getByRole } = render(<ListRow onClick={onClick} label="Row">Row</ListRow>)
    const hit = getByRole('button', { name: 'Row' })
    fireEvent.keyDown(hit, { key: 'ArrowDown' })
    fireEvent.keyDown(hit, { key: 'Escape' })
    expect(onClick).not.toHaveBeenCalled()
  })

  it('interactive rows name their keyboard focus with the shared inset ring', () => {
    const { container } = render(<ListRow onClick={() => {}} label="Row">Row</ListRow>)
    const have = classOf(container.firstElementChild)
    for (const t of ['has-[>button:focus-visible]:ring-2', 'has-[>button:focus-visible]:ring-inset',
      'has-[>button:focus-visible]:ring-primary']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
  })

  it('a static row stays inert — no role, no tab stop, no focus ring', () => {
    const { container } = render(<ListRow>Row</ListRow>)
    const row = container.firstElementChild!
    expect(row.getAttribute('role')).toBeNull()
    expect(row.getAttribute('tabindex')).toBeNull()
    expect(classOf(row)).not.toContain('focus-visible:ring-2')
    expect(classOf(row)).not.toContain('cursor-pointer')
  })
})
