import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { GIDEON_APPS, GideonCollection } from './GideonCollection'
import { APP_MARKS } from './GideonAppMarks'

afterEach(cleanup)

const EXPECTED_ROUTES: Record<string, string> = {
  Research: 'knowledge/reports',
  Slides: 'capabilities/creative?view=production',
  Studio: 'capabilities/media?view=images',
  Writer: 'capabilities/creative?view=works',
  Music: 'capabilities/music',
  Worlds: 'capabilities/experience',
  Knowledge: 'knowledge',
  Journal: 'capabilities/knowledge/journals',
  Health: 'capabilities/wellbeing',
  People: 'capabilities/communications',
  Compass: 'capabilities/identity/goals',
  Automations: 'workflows',
  Code: 'code',
  Agents: 'agents',
  Lab: 'experiments',
  Workspace: 'capabilities/workspace',
  Connections: 'settings/apps',
}

describe('Gideon collection', () => {
  it('contains every approved name once, with a real destination and first action', () => {
    expect(GIDEON_APPS).toHaveLength(17)
    expect(GIDEON_APPS.map((app) => app.name)).toEqual(Object.keys(EXPECTED_ROUTES))
    for (const app of GIDEON_APPS) {
      expect(app.route, app.name).toBe(EXPECTED_ROUTES[app.name])
      expect(app.action.length, app.name).toBeGreaterThan(5)
      expect(app.description.length, app.name).toBeGreaterThan(15)
    }
  })

  it('shows all 17 apps as accessible action targets and retains app management', () => {
    render(<GideonCollection navigate={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'More room for what you do.' })).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /: (Open|Create)/ })).toHaveLength(17)
    expect(screen.getByRole('button', { name: 'Manage apps' })).toBeTruthy()
    for (const name of Object.keys(EXPECTED_ROUTES)) {
      expect(screen.getByRole('button', { name: new RegExp(`^${name}: `) })).toBeTruthy()
    }
  })

  it('shows three selected illustrations and fourteen distinct unframed marks', () => {
    const view = render(<GideonCollection navigate={vi.fn()} />)
    expect(Object.keys(APP_MARKS)).toEqual(GIDEON_APPS.map((app) => app.name))
    expect(new Set(Object.values(APP_MARKS).map((mark) => mark.outline)).size).toBe(17)
    expect(new Set(Object.values(APP_MARKS).map((mark) => mark.accent)).size).toBe(17)
    for (const [index, app] of GIDEON_APPS.entries()) {
      const card = screen.getByRole('button', { name: new RegExp(`^${app.name}: `) })
      const mark = card.querySelector(`svg[data-app-mark="${app.name}"]`)
      expect(card.getAttribute('data-category')).toBe(app.category)
      expect(card.textContent).toContain(String(index + 1).padStart(2, '0'))
      if (['Research', 'Slides', 'Studio'].includes(app.name)) {
        const image = card.querySelector('img.gideon-collection-feature-art')
        expect(card.getAttribute('data-art')).toBe('illustration')
        expect(image?.getAttribute('src')).toBe(`/illustrations/gideon-${app.name.toLowerCase()}.png`)
        expect(image?.getAttribute('alt')).toBe('')
        expect(image?.getAttribute('width')).toBe('1536')
        expect(image?.getAttribute('height')).toBe('1024')
        expect(mark).toBeNull()
      } else {
        expect(card.getAttribute('data-art')).toBe('mark')
        expect(mark?.parentElement?.getAttribute('aria-hidden')).toBe('true')
        expect(mark?.getAttribute('viewBox')).toBe('0 0 160 100')
        expect(mark?.querySelectorAll('path')).toHaveLength(2)
        expect(mark?.querySelector('rect')).toBeNull()
        expect(mark?.querySelectorAll('path')[0].getAttribute('d')).toBe(APP_MARKS[app.name].outline)
        expect(mark?.querySelectorAll('path')[1].getAttribute('d')).toBe(APP_MARKS[app.name].accent)
      }
    }
    expect(view.container.querySelectorAll('img.gideon-collection-feature-art')).toHaveLength(3)
    expect(view.container.querySelectorAll('svg[data-app-mark]')).toHaveLength(14)
    expect(screen.queryAllByRole('img')).toHaveLength(0)
    expect(view.container.querySelectorAll('svg[data-app-mark] [tabindex]')).toHaveLength(0)
  })

  it('keeps mark identities and collection numbers when categories are filtered', async () => {
    render(<GideonCollection navigate={vi.fn()} />)
    const filters = screen.getByRole('navigation', { name: 'App categories' })
    await userEvent.click(within(filters).getByRole('button', { name: 'Everyday' }))
    const selected = GIDEON_APPS.filter((app) => app.category === 'Everyday')
    expect(document.querySelectorAll('svg[data-app-mark]')).toHaveLength(selected.length)
    for (const app of selected) {
      const card = screen.getByRole('button', { name: new RegExp(`^${app.name}: `) })
      expect(card.querySelector('svg')?.getAttribute('data-app-mark')).toBe(app.name)
      expect(card.textContent).toContain(String(GIDEON_APPS.indexOf(app) + 1).padStart(2, '0'))
      expect(card.querySelector('svg')?.closest('[aria-hidden="true"]')).toBeTruthy()
    }
    await userEvent.click(within(filters).getByRole('button', { name: 'All' }))
    expect(document.querySelectorAll('svg[data-app-mark]')).toHaveLength(14)
  })

  it('retains the selected art when filters change', async () => {
    render(<GideonCollection navigate={vi.fn()} />)
    const filters = screen.getByRole('navigation', { name: 'App categories' })
    await userEvent.click(within(filters).getByRole('button', { name: 'Create' }))
    expect(document.querySelectorAll('img.gideon-collection-feature-art')).toHaveLength(2)
    expect(document.querySelectorAll('svg[data-app-mark]')).toHaveLength(3)
    await userEvent.click(within(filters).getByRole('button', { name: 'Think' }))
    expect(document.querySelectorAll('img.gideon-collection-feature-art')).toHaveLength(1)
    expect(document.querySelectorAll('svg[data-app-mark]')).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'Research: Open reports' })).toBeTruthy()
  })

  it.each(Object.entries(EXPECTED_ROUTES))('opens %s in its real destination', async (name, route) => {
    const navigate = vi.fn()
    render(<GideonCollection navigate={navigate} />)
    await userEvent.click(screen.getByRole('button', { name: new RegExp(`^${name}: `) }))
    expect(navigate).toHaveBeenCalledExactlyOnceWith(route)
  })

  it('opens the dedicated Connections page in the hosted edition', async () => {
    const navigate = vi.fn()
    render(<GideonCollection navigate={navigate} connectionsRoute="connections" />)
    await userEvent.click(screen.getByRole('button', { name: 'Connections: Open connections' }))
    expect(navigate).toHaveBeenCalledExactlyOnceWith('connections')
  })

  it('uses the existing install and configuration manager', async () => {
    const navigate = vi.fn()
    render(<GideonCollection navigate={navigate} />)
    await userEvent.click(screen.getByRole('button', { name: 'Manage apps' }))
    expect(navigate).toHaveBeenCalledExactlyOnceWith('apps/manage')
  })

  it('filters categories without changing the source collection', async () => {
    render(<GideonCollection navigate={vi.fn()} />)
    const filters = screen.getByRole('navigation', { name: 'App categories' })
    for (const category of ['Create', 'Think', 'Everyday', 'Build'] as const) {
      await userEvent.click(within(filters).getByRole('button', { name: category }))
      const selected = GIDEON_APPS.filter((app) => app.category === category)
      expect(within(filters).getByRole('button', { name: category }).getAttribute('aria-pressed')).toBe('true')
      expect(screen.getAllByRole('button', { name: /: (Open|Create)/ })).toHaveLength(selected.length)
      for (const app of selected) {
        expect(screen.getByRole('button', { name: new RegExp(`^${app.name}: `) })).toBeTruthy()
      }
    }
    await userEvent.click(within(filters).getByRole('button', { name: 'All' }))
    expect(screen.getAllByRole('button', { name: /: (Open|Create)/ })).toHaveLength(17)
  })

  it('supports keyboard opening from a filtered category', async () => {
    const navigate = vi.fn()
    render(<GideonCollection navigate={navigate} />)
    await userEvent.click(screen.getByRole('button', { name: 'Think' }))
    const lab = screen.getByRole('button', { name: 'Lab: Open experiments' })
    lab.focus()
    await userEvent.keyboard('{Enter}')
    expect(navigate).toHaveBeenCalledExactlyOnceWith('experiments')
  })
})
