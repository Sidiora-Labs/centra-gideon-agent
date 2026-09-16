import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { Popover } from './Popover'


const VIEWPORT_H = 800

function anchorAt(top: number, height = 32) {
  Object.defineProperty(window, 'innerHeight', { value: VIEWPORT_H, configurable: true })
  Object.defineProperty(window, 'innerWidth', { value: 1280, configurable: true })
  Element.prototype.getBoundingClientRect = function () {
    return {
      top, bottom: top + height, left: 100, right: 300,
      width: 200, height, x: 100, y: top, toJSON: () => ({}),
    } as DOMRect
  }
}

function openMenu(placement?: 'top' | 'bottom') {
  render(
    <Popover
      placement={placement}
      trigger={(_open, toggle) => <button onClick={toggle}>Open</button>}
    >
      {() => <div data-testid="menu-body">rows</div>}
    </Popover>,
  )
  act(() => { screen.getByText('Open').click() })
  return screen.getByTestId('menu-body').parentElement as HTMLElement
}

const realRect = Element.prototype.getBoundingClientRect

describe('Popover placement flips to the side with room', () => {
  beforeEach(() => { Object.defineProperty(window, 'innerHeight', { value: VIEWPORT_H, configurable: true }) })
  afterEach(() => { Element.prototype.getBoundingClientRect = realRect })

  it('flips a top-preferring menu DOWN when the trigger is near the top of the page', () => {
    anchorAt(40)
    const flyout = openMenu('top')
    expect(flyout.className).toContain('top-full')
    expect(flyout.className).not.toContain('bottom-full')
  })

  it('keeps a top-preferring menu UP when there is room above', () => {
    anchorAt(700)
    const flyout = openMenu('top')
    expect(flyout.className).toContain('bottom-full')
    expect(flyout.className).not.toContain('top-full')
  })

  it('flips a bottom-preferring menu UP when the trigger is near the bottom', () => {
    anchorAt(VIEWPORT_H - 60)
    const flyout = openMenu('bottom')
    expect(flyout.className).toContain('bottom-full')
  })

  it('keeps a bottom-preferring menu DOWN when there is room below', () => {
    anchorAt(80)
    const flyout = openMenu('bottom')
    expect(flyout.className).toContain('top-full')
  })

  it('defaults to opening upward, preserving the pre-existing composer behavior', () => {
    anchorAt(700)
    const flyout = openMenu()
    expect(flyout.className).toContain('bottom-full')
  })

  it('sets the transform origin to the side it actually used', () => {
    anchorAt(40)
    const flyout = openMenu('top')
    expect(flyout.style.transformOrigin).toContain('top')
  })
})
