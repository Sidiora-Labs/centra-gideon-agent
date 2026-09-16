import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Home, ListChecks } from 'lucide-react'
import { NavRail } from './NavRail'


const ITEMS = [
  { id: 'dashboard', label: 'Home', icon: Home },
  { id: 'tasks', label: 'Tasks', icon: ListChecks },
]
const drawer = (c: HTMLElement) => c.querySelector('[role="dialog"][aria-label="Navigation"]')!

describe('NavRail overlay drawer', () => {
  it('is inert while closed, so the tab order skips it', () => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed={false} overlay overlayOpen={false} />)
    const d = drawer(container)
    expect(d).toHaveAttribute('inert')
    expect(d).toHaveAttribute('aria-hidden', 'true')
    expect(d.querySelectorAll('button').length).toBeGreaterThan(0)
  })

  it('drops inert entirely when open, so every nav item stays reachable', () => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed={false} overlay overlayOpen />)
    const d = drawer(container)
    expect(d.hasAttribute('inert')).toBe(false)
    expect(d).toHaveAttribute('aria-hidden', 'false')
    const first = d.querySelector('button')!
    first.focus()
    expect(document.activeElement).toBe(first)
  })
})


const badged = (extra: Partial<{ badge: string; badgeLabel: string }>) => [
  { id: 'projects', label: 'Projects', icon: Home, ...extra },
]

describe('a nav count badge says what it counts', () => {
  it('announces the meaning, not just the number', () => {
    const { getByRole } = render(
      <NavRail items={badged({ badge: '1', badgeLabel: '1 active loop' })} activeId="x" onSelect={() => {}} collapsed={false} />,
    )
    expect(getByRole('button', { name: 'Projects, 1 active loop' })).toBeTruthy()
  })

  it('spells it out in the tooltip too, as the bell does', () => {
    const { getByRole } = render(
      <NavRail items={badged({ badge: '1', badgeLabel: '1 active loop' })} activeId="x" onSelect={() => {}} collapsed={false} />,
    )
    expect(getByRole('button', { name: /Projects/ }).getAttribute('title')).toBe('1 active loop')
  })

  it('still announces the COUNT when the unit is not the shell to name', () => {
    const { getByRole } = render(
      <NavRail items={badged({ badge: '3' })} activeId="x" onSelect={() => {}} collapsed={false} />,
    )
    expect(getByRole('button', { name: 'Projects, 3' })).toBeTruthy()
  })

  it('leaves an unbadged item exactly as it was', () => {
    const { getByRole } = render(
      <NavRail items={badged({})} activeId="x" onSelect={() => {}} collapsed={false} />,
    )
    const b = getByRole('button', { name: 'Projects' })
    expect(b.getAttribute('title')).toBeNull()
  })

  it('carries BOTH label and meaning while collapsed, where neither is visible', () => {
    const { getByRole } = render(
      <NavRail items={badged({ badge: '1', badgeLabel: '1 active loop' })} activeId="x" onSelect={() => {}} collapsed />,
    )
    expect(getByRole('button', { name: 'Projects, 1 active loop' }).getAttribute('title')).toBe('Projects, 1 active loop')
  })

  it('keeps the badge VISIBLY a bare number (this is a naming fix, not a redesign)', () => {
    const { getByRole } = render(
      <NavRail items={badged({ badge: '1', badgeLabel: '1 active loop' })} activeId="x" onSelect={() => {}} collapsed={false} />,
    )
    expect(getByRole('button', { name: /Projects/ }).textContent).toBe('Projects1')
  })
})


describe('the shell supplies the meaning it owns', () => {
  const app = readFileSync(join(process.cwd(), "src/app/shell/App.tsx"), 'utf8')

  it('names the Projects badge as the active-LOOP count', () => {
    expect(app, 'the badge is activeLoops, so say so').toMatch(/badgeLabel: `\$\{activeLoops\} active loop/)
  })

  it('pluralises rather than shipping "1 active loops"', () => {
    expect(app).toMatch(/activeLoops === 1 \? '' : 's'/)
  })

  it('claims the Store badge is updates ONLY when nothing else is summed in', () => {
    expect(app).toMatch(/appBadgeTotal === updatesCount/)
    expect(app).toMatch(/app update\$\{updatesCount === 1 \? '' : 's'\} available/)
  })

  it('has not started labelling a badge it cannot explain', () => {
    const perApp = /const badge = appBadges\[[\s\S]{0,120}?badge \? \{ \.\.\.ai, badge: String\(badge\) \}/.test(app)
    expect(perApp, 'per-app SDK badges must not be given an invented unit').toBe(true)
  })
})
