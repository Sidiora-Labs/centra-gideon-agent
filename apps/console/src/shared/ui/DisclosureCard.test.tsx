import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Globe } from 'lucide-react'
import { DisclosureCard } from './DisclosureCard'


describe('DisclosureCard', () => {
  it('starts collapsed and does not render the body', () => {
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="tavily" active count={3}>
        <p>body content</p>
      </DisclosureCard>,
    )
    expect(screen.getByRole('button', { name: /General search/ })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('body content'), 'a collapsed card must not mount its body').toBeNull()
  })

  it('discloses the body on click and flips aria-expanded', async () => {
    const user = userEvent.setup()
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="tavily" active count={3}>
        <p>body content</p>
      </DisclosureCard>,
    )
    const header = screen.getByRole('button', { name: /General search/ })
    await user.click(header)
    expect(header).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('body content')).toBeInTheDocument()
  })

  it('points aria-controls at the region it actually discloses', async () => {
    const user = userEvent.setup()
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="tavily" active count={3}>
        <p>body content</p>
      </DisclosureCard>,
    )
    const header = screen.getByRole('button', { name: /General search/ })
    const controls = header.getAttribute('aria-controls')
    expect(controls, 'the header must name its region').toBeTruthy()
    await user.click(header)
    const body = document.getElementById(controls!)
    expect(body, 'and that id must resolve to the disclosed body').not.toBeNull()
    expect(body!.textContent).toContain('body content')
  })

  it('gives two cards on one page distinct region ids', () => {
    render(
      <>
        <DisclosureCard icon={Globe} label="First" subtitle="a" active={false}><p>one</p></DisclosureCard>
        <DisclosureCard icon={Globe} label="Second" subtitle="b" active={false}><p>two</p></DisclosureCard>
      </>,
    )
    const a = screen.getByRole('button', { name: /First/ }).getAttribute('aria-controls')
    const b = screen.getByRole('button', { name: /Second/ }).getAttribute('aria-controls')
    expect(a).not.toBe(b)
  })

  it('is keyboard-operable, because the header is a real button', async () => {
    const user = userEvent.setup()
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="tavily" active={false}>
        <p>body content</p>
      </DisclosureCard>,
    )
    await user.tab()
    const header = screen.getByRole('button', { name: /General search/ })
    expect(header).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(header).toHaveAttribute('aria-expanded', 'true')
  })

  it('draws its focus ring INSIDE the card, because the card clips', () => {
    // global rail's outward `outline-offset: 2px` drew the whole ring outside the clip and nothing
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="tavily" active={false}>
        <p>body</p>
      </DisclosureCard>,
    )
    const header = screen.getByRole('button', { name: /General search/ })
    expect(header.className).toContain('focus-visible:-outline-offset-2')
  })

  it('renders the count pill when there is something to offer', () => {
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="tavily" active count={7}>
        <p>body</p>
      </DisclosureCard>,
    )
    expect(screen.getByText('7 available')).toBeInTheDocument()
  })

  it('suppresses the pill at zero rather than announcing "0 available"', () => {
    render(
      <DisclosureCard icon={Globe} label="General search" subtitle="none" active={false} count={0}>
        <p>body</p>
      </DisclosureCard>,
    )
    expect(screen.queryByText(/available/)).toBeNull()
  })

  it('takes a custom count noun', () => {
    render(
      <DisclosureCard icon={Globe} label="X" subtitle="y" active count={2} countLabel="bound">
        <p>body</p>
      </DisclosureCard>,
    )
    expect(screen.getByText('2 bound')).toBeInTheDocument()
  })

  it('accepts a node subtitle, so an unbound state can have its own voice', () => {
    render(
      <DisclosureCard icon={Globe} label="News search"
        subtitle={<span className="italic">none — falls back to General</span>} active={false}>
        <p>body</p>
      </DisclosureCard>,
    )
    expect(screen.getByText('none — falls back to General')).toBeInTheDocument()
  })
})
