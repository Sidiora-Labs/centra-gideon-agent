import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SidePanel } from './SidePanel'


describe('SidePanel is a named landmark region', () => {
  it('the docked panel exposes role="region" named by its title', () => {
    render(<SidePanel title="Ship the release" storeKey="test-panel-w" onClose={() => {}}>body</SidePanel>)
    const region = screen.getByRole('region', { name: 'Ship the release' })
    expect(region).toBeTruthy()
  })

  it('the region contains a heading named by its title, so heading-nav reaches it', () => {
    render(<SidePanel title="Ship the release" storeKey="test-panel3-w" onClose={() => {}}>body</SidePanel>)
    const heading = screen.getByRole('heading', { name: 'Ship the release' })
    expect(heading.tagName).toBe('H2')
    const region = screen.getByRole('region', { name: 'Ship the release' })
    expect(region.getAttribute('aria-labelledby')).toBe(heading.id)
  })

  it('the name comes from the title element, not a duplicated aria-label string', () => {
    const { container } = render(
      <SidePanel title="Chat history" storeKey="test-panel2-w" onClose={() => {}}>body</SidePanel>,
    )
    const region = screen.getByRole('region', { name: 'Chat history' })
    const labelledby = region.getAttribute('aria-labelledby')
    expect(labelledby, 'region must be labelled by an element, not a raw string').toBeTruthy()
    expect(region.getAttribute('aria-label'), 'no raw aria-label — labelledby is the source').toBeNull()
    const titleEl = container.querySelector(`#${CSS.escape(labelledby!)}`)
    expect(titleEl?.textContent).toBe('Chat history')
  })
})
