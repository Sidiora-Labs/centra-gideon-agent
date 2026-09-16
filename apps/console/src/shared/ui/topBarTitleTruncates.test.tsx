import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { TopBar } from './TopBar'


function leftSlot(contentAligned: boolean): HTMLElement {
  const { container } = render(
    <TopBar contentAligned={contentAligned}
      left={<span data-type="title-l">A very long page title that cannot possibly fit</span>}
      right={<button type="button">Action</button>} />,
  )
  const el = container.querySelector<HTMLElement>('[data-header-left]')
  if (!el) throw new Error('TopBar rendered no [data-header-left] slot')
  return el
}

describe.each([
  ['default', false],
  ['contentAligned', true],
])('TopBar left slot (%s)', (_label, contentAligned) => {
  it('still shrinks (the flex bound the truncation depends on)', () => {
    const slot = leftSlot(contentAligned)
    expect(slot.className).toMatch(/\bmin-w-0\b/)
    expect(slot.className).toMatch(/\bflex-1\b/)
  })

  it('truncates its title with an ellipsis and keeps a gap before the controls', () => {
    const slot = leftSlot(contentAligned)
    expect(slot.className).toMatch(/\[&_\[data-type\]\]:truncate/)
    expect(slot.className).toMatch(/\[&_\[data-type\]\]:pr-s/)
  })

  it('lets the shrink propagate through a nested wrapper', () => {
    const slot = leftSlot(contentAligned)
    expect(slot.className).toMatch(/\[&_div\]:min-w-0/)
    expect(slot.className).toMatch(/\[&_\[data-type\]\]:min-w-0/)
  })

  it('does NOT clip the slot itself (that would hide control rows, not truncate text)', () => {
    const slot = leftSlot(contentAligned)
    const own = slot.className.split(/\s+/).filter((c) => !c.startsWith('[&'))
    expect(own, `slot's own classes: ${own.join(' ')}`).not.toContain('overflow-hidden')
  })
})
