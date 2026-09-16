import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ContextLedger } from './ContextLedger'


let scrolled: Element[] = []
const NO_SCROLL = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollIntoView')

beforeEach(() => {
  scrolled = []
  Element.prototype.scrollIntoView = function (this: Element) { scrolled.push(this) }
})
afterEach(() => {
  if (NO_SCROLL) Object.defineProperty(Element.prototype, 'scrollIntoView', NO_SCROLL)
  else delete (Element.prototype as unknown as Record<string, unknown>).scrollIntoView
})

const ledger = (over: Partial<Parameters<typeof ContextLedger>[0]> = {}) =>
  render(
    <ContextLedger
      fed="Recalled relevant context · 1,204 chars"
      learned="Learned: prefers tabs over spaces"
      learnedOrigin="proposal"
      stats="12.4s · 3,102 tokens"
      {...over}
    />,
  )

const chip = () => screen.getByRole('button')
const approveLink = () => screen.getByRole('link', { name: /Review in Skill proposals/ })

describe('LV-2 — the learned chip reaches the approve/edit surface in ONE action', () => {
  it('VACUITY FLOOR — the target really is behind a CLOSED disclosure at first paint', () => {
    ledger()
    expect(chip().getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.queryByText(/prefers tabs over spaces/)).toBeNull()
    expect(scrolled).toHaveLength(0)
    expect(chip().textContent).toContain('learned 1')
  })

  it('HALF A — one tap opens the disclosure and renders the right approve/edit link', () => {
    ledger()
    fireEvent.click(chip())
    expect(chip().getAttribute('aria-expanded')).toBe('true')
    expect(approveLink().getAttribute('href')).toBe('#/skills?mode=proposals')
  })

  it('HALF B — the same tap brings that link into view and puts FOCUS on it', () => {
    ledger()
    const trigger = chip()
    trigger.focus()
    fireEvent.click(trigger)
    const link = approveLink()
    expect(scrolled).toContain(link)
    expect(document.activeElement).toBe(link)
    expect(link.tagName).toBe('A')
  })

  it('a lesson tap lands in the Memory studio instead — the routing is not one constant', () => {
    ledger({ learnedOrigin: 'lesson', learned: 'Learned: keep replies short' })
    fireEvent.click(chip())
    const link = screen.getByRole('link', { name: /Review lessons in Memory/ })
    expect(link.getAttribute('href')).toBe('#/settings/memory?tab=studio')
    expect(document.activeElement).toBe(link)
    expect(scrolled).toContain(link)
  })

  it('DEGRADE — an unknown origin still opens, and steals neither focus nor scroll', () => {
    ledger({ learnedOrigin: 'sop' })
    const trigger = chip()
    trigger.focus()
    fireEvent.click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText(/prefers tabs over spaces/)).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
    expect(document.activeElement).toBe(trigger)
    expect(scrolled).toHaveLength(0)
  })

  it('a turn that learned nothing moves no focus (the effect is gated, not unconditional)', () => {
    ledger({ learned: undefined, learnedOrigin: undefined })
    const trigger = chip()
    trigger.focus()
    fireEvent.click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(document.activeElement).toBe(trigger)
    expect(scrolled).toHaveLength(0)
  })

  it('collapsing again does not re-reach anything (the effect is gated on open)', () => {
    ledger()
    fireEvent.click(chip())
    expect(document.activeElement).toBe(approveLink())
    expect(scrolled).toHaveLength(1)
    fireEvent.click(chip())
    expect(chip().getAttribute('aria-expanded')).toBe('false')
    expect(scrolled).toHaveLength(1)
  })

  it('the hover text says what the tap does, and only when there is somewhere to land', () => {
    const withSurface = ledger()
    expect(chip().getAttribute('title')).toContain('jumps to where you can review it')
    withSurface.unmount()
    ledger({ learnedOrigin: 'sop' })
    expect(chip().getAttribute('title')).not.toContain('jumps to')
  })
})
