import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SUBPAGES } from './SettingsPage'
import { SettingsNavigation } from './settingsUI'

afterEach(cleanup)

describe('grouped settings navigation', () => {
  it('keeps every existing panel reachable from the section menu', () => {
    render(<SettingsNavigation items={SUBPAGES} current="account" go={vi.fn()} />)
    const picker = screen.getByRole('combobox', { name: 'Settings section' })
    const options = within(picker).getAllByRole('option')
    expect(options.map((option) => option.getAttribute('value')).sort()).toEqual(SUBPAGES.map((page) => page.id).sort())
    expect(new Set(options.map((option) => option.getAttribute('value'))).size).toBe(SUBPAGES.length)
    expect(within(picker).getAllByRole('group').map((group) => group.getAttribute('label')))
      .toEqual(['Personal', 'Connections & abilities', 'Data & system'])
  })

  it('navigates directly to another persisted panel without replacing its form', async () => {
    const go = vi.fn()
    render(<SettingsNavigation items={SUBPAGES} current="account" go={go} />)
    await userEvent.click(within(screen.getByRole('navigation', { name: 'Settings sections' }))
      .getByRole('button', { name: 'Security' }))
    expect(go).toHaveBeenCalledExactlyOnceWith('security')
    expect(screen.getByRole('button', { name: 'Account' }).getAttribute('aria-current')).toBe('page')
  })

  it('changes sections through the mobile picker', async () => {
    const go = vi.fn()
    render(<SettingsNavigation items={SUBPAGES} current="account" go={go} />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Settings section' }), 'voice')
    expect(go).toHaveBeenCalledExactlyOnceWith('voice')
  })

  it('preserves URL-selected section state after remount', () => {
    const { unmount } = render(<SettingsNavigation items={SUBPAGES} current="security" go={vi.fn()} />)
    expect(screen.getByRole('combobox', { name: 'Settings section' })).toHaveProperty('value', 'security')
    unmount()
    render(<SettingsNavigation items={SUBPAGES} current="notifications" go={vi.fn()} />)
    expect(screen.getByRole('combobox', { name: 'Settings section' })).toHaveProperty('value', 'notifications')
    expect(screen.getByRole('button', { name: 'Notifications' }).getAttribute('aria-current')).toBe('page')
  })

  it('renders only policy-allowed items supplied by the host', () => {
    const allowed = SUBPAGES.filter((page) => !['providers', 'secrets', 'routing'].includes(page.id))
    render(<SettingsNavigation items={allowed} current="models" go={vi.fn()} />)
    const picker = screen.getByRole('combobox', { name: 'Settings section' })
    expect(within(picker).getAllByRole('option')).toHaveLength(allowed.length)
    for (const denied of ['providers', 'secrets', 'routing']) {
      expect(within(picker).queryByRole('option', { name: SUBPAGES.find((page) => page.id === denied)!.label })).toBeNull()
    }
    expect(within(picker).getByRole('option', { name: 'Models' })).toBeTruthy()
  })
})
