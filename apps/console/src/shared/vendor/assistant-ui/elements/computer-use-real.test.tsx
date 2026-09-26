import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ComputerUse, type ComputerStep } from './computer-use'

afterEach(cleanup)

const knownSteps: ComputerStep[] = [
  { id: 'open', action: 'click', target: 'Open menu', x: 0, y: 80 },
  { id: 'search', action: 'type', target: 'Search field', x: 45, y: 60 },
]

function marks(container: HTMLElement) {
  return [...container.querySelectorAll('span[style*="opacity"]')]
}

function cursor(container: HTMLElement) {
  return container.querySelector('svg[style*="left"]')
}

describe('ComputerUse with recorded computer actions', () => {
  it('retains the donor URL, step details, children, trail and cursor when positions are supplied', () => {
    const { container } = render(
      <ComputerUse url="https://gideon.centra.ag" steps={knownSteps} activeIndex={0}>
        <span>Recorded browser frame</span>
      </ComputerUse>,
    )
    expect(screen.getByText('https://gideon.centra.ag')).toBeTruthy()
    expect(screen.getByText('Recorded browser frame')).toBeTruthy()
    expect(screen.getByText('click')).toBeTruthy()
    expect(screen.getByText('Open menu')).toBeTruthy()
    expect(screen.getByText('1/2')).toBeTruthy()
    expect(marks(container)).toHaveLength(1)
    expect(marks(container)[0]?.getAttribute('style')).toContain('left: 0%')
    expect(cursor(container)?.getAttribute('style')).toContain('top: 80%')
  })

  it('omits the address row when no URL is recorded while retaining a known pointer', () => {
    const { container } = render(
      <ComputerUse steps={knownSteps} activeIndex={1}><span>Live action</span></ComputerUse>,
    )
    expect(container.querySelector('[data-slot="computer-use"] > div:first-child')?.textContent).toContain('Live action')
    expect(screen.queryByText('https://gideon.centra.ag')).toBeNull()
    expect(screen.getByText('Search field')).toBeTruthy()
    expect(screen.getByText('2/2')).toBeTruthy()
    expect(marks(container)).toHaveLength(2)
    expect(cursor(container)?.getAttribute('style')).toContain('left: 45%')
  })

  it('omits pointer marks when coordinates are unknown but keeps action and children', () => {
    const steps: ComputerStep[] = [{ id: 'inspect', action: 'inspect', target: 'Status pane' }]
    const { container } = render(
      <ComputerUse steps={steps} activeIndex={0}><span>Accessible page content</span></ComputerUse>,
    )
    expect(screen.getByText('Accessible page content')).toBeTruthy()
    expect(screen.getByText('inspect')).toBeTruthy()
    expect(screen.getByText('Status pane')).toBeTruthy()
    expect(screen.getByText('1/1')).toBeTruthy()
    expect(marks(container)).toHaveLength(0)
    expect(cursor(container)).toBeNull()
  })

  it('shows only complete historical positions and never invents a partial active pointer', () => {
    const steps: ComputerStep[] = [
      { id: 'open', action: 'click', target: 'Menu', x: 20, y: 30 },
      { id: 'read', action: 'inspect', target: 'Result', x: 60 },
      { id: 'scroll', action: 'scroll', target: 'List', y: 70 },
    ]
    const { container, rerender } = render(
      <ComputerUse url="" steps={steps} activeIndex={2}><span>Page</span></ComputerUse>,
    )
    expect(screen.getByText('3/3')).toBeTruthy()
    expect(screen.getByText('List')).toBeTruthy()
    expect(marks(container)).toHaveLength(1)
    expect(marks(container)[0]?.getAttribute('style')).toContain('left: 20%')
    expect(cursor(container)).toBeNull()
    expect(container.querySelector('[data-slot="computer-use"] > div:first-child')?.textContent).toContain('Page')
    rerender(<ComputerUse steps={steps} activeIndex={0}><span>Page</span></ComputerUse>)
    expect(cursor(container)?.getAttribute('style')).toContain('left: 20%')
    expect(screen.getByText('1/3')).toBeTruthy()
  })
})
