import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { GIDEON_APPS, GideonCollection } from './GideonCollection'

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
