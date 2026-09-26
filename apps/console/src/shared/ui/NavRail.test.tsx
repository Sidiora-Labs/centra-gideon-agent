import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
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
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} onSearch={() => {}} collapsed={false} overlay overlayOpen={false} />)
    const d = drawer(container)
    expect(d).toHaveAttribute('inert')
    expect(d).toHaveAttribute('aria-hidden', 'true')
    expect(d.querySelectorAll('button').length).toBeGreaterThan(0)
  })

  it('drops inert entirely when open, so every nav item stays reachable', () => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} onSearch={() => {}} collapsed={false} overlay overlayOpen />)
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
      <NavRail items={badged({ badge: '1', badgeLabel: '1 active loop' })} activeId="x" onSelect={() => {}} onSearch={() => {}} collapsed={false} />,
    )
    expect(getByRole('button', { name: 'Projects, 1 active loop' })).toBeTruthy()
  })

  it('spells it out in the tooltip too, as the bell does', () => {
    const { getByRole } = render(
      <NavRail items={badged({ badge: '1', badgeLabel: '1 active loop' })} activeId="x" onSelect={() => {}} onSearch={() => {}} collapsed={false} />,
    )
    expect(getByRole('button', { name: /Projects/ }).getAttribute('title')).toBe('1 active loop')
  })

  it('still announces the COUNT when the unit is not the shell to name', () => {
    const { getByRole } = render(
      <NavRail items={badged({ badge: '3' })} activeId="x" onSelect={() => {}} onSearch={() => {}} collapsed={false} />,
    )
    expect(getByRole('button', { name: 'Projects, 3' })).toBeTruthy()
  })

  it('leaves an unbadged item exactly as it was', () => {
    const { getByRole } = render(
      <NavRail items={badged({})} activeId="x" onSelect={() => {}} onSearch={() => {}} collapsed={false} />,
    )
    const b = getByRole('button', { name: 'Projects' })
    expect(b.getAttribute('title')).toBeNull()
  })

  it('carries BOTH label and meaning while collapsed, where neither is visible', () => {
    const { getByRole } = render(
      <NavRail items={badged({ badge: '1', badgeLabel: '1 active loop' })} activeId="x" onSelect={() => {}} onSearch={() => {}} collapsed />,
    )
    expect(getByRole('button', { name: 'Projects, 1 active loop' }).getAttribute('title')).toBe('Projects, 1 active loop')
  })

  it('keeps the badge VISIBLY a bare number (this is a naming fix, not a redesign)', () => {
    const { getByRole } = render(
      <NavRail items={badged({ badge: '1', badgeLabel: '1 active loop' })} activeId="x" onSelect={() => {}} onSearch={() => {}} collapsed={false} />,
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


describe('label-led rail navigation', () => {
  const links = [
    { id: 'chat/new', label: 'New conversation', icon: Home, section: 'Your space' },
    { id: 'apps', label: 'All apps', icon: ListChecks, section: 'Your apps' },
    { id: 'chat/recent-1', label: 'Real conversation', icon: Home, section: 'Recent' },
    { id: 'settings', label: 'Your account', icon: ListChecks, pinBottom: true },
  ]

  it('keeps the explicit search callback separate from route selection', () => {
    const onSearch = vi.fn()
    const onSelect = vi.fn()
    const view = render(<NavRail items={links} activeId="apps/manage" onSearch={onSearch} onSelect={onSelect} collapsed={false} />)
    fireEvent.click(view.getByRole('button', { name: 'Search' }))
    expect(onSearch).toHaveBeenCalledOnce()
    expect(onSelect).not.toHaveBeenCalled()
    fireEvent.click(view.getByRole('button', { name: 'Real conversation' }))
    expect(onSelect).toHaveBeenCalledWith('chat/recent-1')
    expect(onSearch).toHaveBeenCalledOnce()
  })

  it('shows actual section labels and marks a capability subroute current', () => {
    const view = render(<NavRail items={links} activeId="apps/manage" onSearch={() => {}} onSelect={() => {}} collapsed={false} />)
    expect(view.getByText('Your space')).toBeTruthy()
    expect(view.getByText('Your apps')).toBeTruthy()
    expect(view.getByText('Recent')).toBeTruthy()
    expect(view.getByText('Account')).toBeTruthy()
    expect(view.getByRole('button', { name: 'All apps' })).toHaveAttribute('aria-current', 'page')
    expect(view.getByRole('button', { name: 'New conversation' })).not.toHaveAttribute('aria-current')
  })

  it('retains section headings in starter disclosure mode', () => {
    const view = render(<NavRail items={links} activeId="chat/new" onSearch={() => {}} onSelect={() => {}} collapsed={false}
      disclosure={{ expanded: false, moreCount: 12, onToggle: () => {} }} />)
    expect(view.getByText('Your space')).toBeTruthy()
    expect(view.getByText('Your apps')).toBeTruthy()
    expect(view.getByText('Recent')).toBeTruthy()
    expect(view.getByRole('button', { name: /Everything, show 12 more surfaces/ })).toHaveAttribute('aria-expanded', 'false')
  })

  it('shows full labels in an overlay even when the desktop rail is collapsed', () => {
    const view = render(<NavRail items={links} activeId="chat/new" onSearch={() => {}} onSelect={() => {}} collapsed overlay overlayOpen />)
    expect(view.getByRole('dialog', { name: 'Navigation' })).not.toHaveAttribute('inert')
    expect(view.getByText('Real conversation')).toBeTruthy()
    expect(view.getByText('Search')).toBeTruthy()
  })

  it('moves focus into the open drawer and returns it to the trigger on close', () => {
    const trigger = document.createElement('button')
    document.body.append(trigger)
    trigger.focus()
    const props = { items: links, activeId: 'chat/new', onSearch: () => {}, onSelect: () => {}, collapsed: false, overlay: true }
    const view = render(<NavRail {...props} overlayOpen={false} />)
    view.rerender(<NavRail {...props} overlayOpen />)
    expect(view.getByRole('dialog', { name: 'Navigation' }).contains(document.activeElement)).toBe(true)
    view.rerender(<NavRail {...props} overlayOpen={false} />)
    expect(document.activeElement).toBe(trigger)
    trigger.remove()
  })

  it('supports keyboard resizing and persists the chosen width', () => {
    localStorage.setItem('nav-width-v2', '196')
    const view = render(<NavRail items={links} activeId="chat/new" onSearch={() => {}} onSelect={() => {}} collapsed={false} />)
    const resize = view.getByRole('separator')
    const before = Number(resize.getAttribute('aria-valuenow'))
    fireEvent.keyDown(resize, { key: 'ArrowRight' })
    expect(Number(resize.getAttribute('aria-valuenow'))).toBe(before + 16)
    expect(localStorage.getItem('nav-width-v2')).toBe(String(before + 16))
  })
})
