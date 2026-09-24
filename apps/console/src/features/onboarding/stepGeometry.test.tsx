import { render, screen, fireEvent } from '@testing-library/react'
import { User } from 'lucide-react'
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { StepRow } from './StepStack'

function Steps({ active }: { active: number }) {
  return <ol>{['Your name', 'Bring your setup over', 'Essential apps', 'Try one', 'All set'].map((title, index) =>
    <StepRow key={title} index={index} icon={User} title={title}
      state={index === active ? 'active' : index < active ? 'done' : 'upcoming'}>
      <input autoFocus aria-label={`Input ${index}`} />
    </StepRow>)}</ol>
}

describe('onboarding geometry and focus contract', () => {
  it('focuses each newly active h2, including backwards navigation', () => {
    const view = render(<Steps active={0} />)
    for (const [index, title] of ['Your name', 'Bring your setup over', 'Essential apps', 'Try one', 'All set'].entries()) {
      view.rerender(<Steps active={index} />)
      const heading = screen.getByRole('heading', { name: title, level: 2 })
      expect(heading).toHaveFocus()
      expect(heading).toHaveAttribute('tabindex', '-1')
    }
    view.rerender(<Steps active={0} />)
    expect(screen.getByRole('heading', { name: 'Your name', level: 2 })).toHaveFocus()
  })

  it('does not steal focus back during edits within the same step', () => {
    const view = render(<Steps active={0} />)
    const field = screen.getByRole('textbox', { name: 'Input 0' })
    field.focus()
    fireEvent.change(field, { target: { value: 'Ada' } })
    view.rerender(<Steps active={0} />)
    expect(field).toHaveFocus()
  })

  it('limits the hanging indent to sm and wider', () => {
    const { container } = render(<Steps active={0} />)
    const body = container.querySelector('[class*="sm:ml-"]')!
    expect(body).toBeTruthy()
    expect(body.className.split(/\s+/)).toContain('sm:ml-[4.75rem]')
    expect(body.className.split(/\s+/).some((name) => /^(ml-|pl-|ps-|ms-)/.test(name))).toBe(false)
  })

  it('provides an isolated four-viewport browser gate without API substitutes', () => {
    const gate = readFileSync('e2e/onboardingGeometry.spec.ts', 'utf8')
    expect([...gate.matchAll(/\{ width: \d+, height: \d+ \}/g)]).toHaveLength(4)
    expect(gate).toContain("await checkStep(page, 'All set')")
    expect(gate).toContain('toBeFocused()')
    expect(gate).not.toMatch(/\.route\(|\.fulfill\(/)
    const config = readFileSync('playwright.onboarding.config.ts', 'utf8')
    expect(config).toContain('workers: 1')
    expect(config).toContain('fullyParallel: false')
  })
})
