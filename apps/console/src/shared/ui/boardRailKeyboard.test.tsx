import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Columns3 } from 'lucide-react'
import { CollapsedBoardColumn, CollapseColumnButton } from './BoardCollapse'


const rail = (onExpand?: () => void) =>
  render(<CollapsedBoardColumn icon={Columns3} label="In progress" count={0} onExpand={onExpand} />)

describe('a collapsed board rail is operable by keyboard', () => {
  it('takes a tab stop', () => {
    rail(vi.fn())
    const el = screen.getByRole('button')
    expect(el.tabIndex, 'role=button with no tab stop is unreachable').toBe(0)
    el.focus()
    expect(document.activeElement).toBe(el)
  })

  it('expands on Enter', () => {
    const onExpand = vi.fn()
    rail(onExpand)
    fireEvent.keyDown(screen.getByRole('button'), { key: 'Enter' })
    expect(onExpand).toHaveBeenCalledTimes(1)
  })

  it('expands on Space, and swallows the scroll', () => {
    const onExpand = vi.fn()
    rail(onExpand)
    const ev = createEvent()
    fireEvent(screen.getByRole('button'), ev)
    expect(onExpand).toHaveBeenCalledTimes(1)
    expect(ev.defaultPrevented, 'Space must not also scroll the page').toBe(true)
  })

  it('ignores other keys', () => {
    const onExpand = vi.fn()
    rail(onExpand)
    fireEvent.keyDown(screen.getByRole('button'), { key: 'a' })
    fireEvent.keyDown(screen.getByRole('button'), { key: 'Escape' })
    expect(onExpand).not.toHaveBeenCalled()
  })

  it('keeps the COUNT in its accessible name, and says what activating it does', () => {
    rail(vi.fn())
    expect(screen.getByRole('button', { name: 'In progress, 0 — expand column' })).toBeTruthy()
  })

  it('announces itself as the collapsed half of a disclosure', () => {
    rail(vi.fn())
    expect(screen.getByRole('button').getAttribute('aria-expanded')).toBe('false')
  })

  it('stays a plain non-interactive rail when it cannot be expanded', () => {
    const { container } = rail(undefined)
    expect(screen.queryByRole('button')).toBeNull()
    const div = container.firstElementChild as HTMLElement
    expect(div.getAttribute('tabindex')).toBeNull()
    expect(div.getAttribute('aria-expanded')).toBeNull()
  })
})

describe('the two halves of the disclosure agree', () => {
  it('the collapse control announces the expanded state', () => {
    render(<CollapseColumnButton onCollapse={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Collapse column' }).getAttribute('aria-expanded')).toBe('true')
  })
})

function createEvent(): KeyboardEvent {
  return new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true })
}
