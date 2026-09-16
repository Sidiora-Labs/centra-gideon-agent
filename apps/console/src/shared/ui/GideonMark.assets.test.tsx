import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ThemeProvider, themeReducer, resolveMode, useMode } from '../../app/shell/theme'
import { GideonMark } from './GideonMark'
import { gideonAssets } from './gideonIdentity'

function Identity() {
  const { toggle, setPreference } = useMode()
  return <><GideonMark size={48} idGradient="existing-caller" blob /><button onClick={toggle}>Toggle</button><button onClick={() => setPreference('auto')}>Auto</button></>
}

beforeEach(() => {
  localStorage.removeItem('mode')
  const favicon = document.createElement('link')
  favicon.rel = 'icon'
  favicon.href = '/gideon.svg'
  favicon.dataset.identityTest = 'true'
  document.head.append(favicon)
})
afterEach(() => {
  localStorage.removeItem('mode')
  document.querySelectorAll('[data-identity-test], [data-gideon-appearance]').forEach((item) => item.remove())
})
const activeFavicon = () => document.querySelector<HTMLLinkElement>('[data-gideon-appearance]')?.getAttribute('href')

describe('supplied Gideon artwork follows real appearance state', () => {
  it('changes the mark and favicon together when appearance is toggled', () => {
    render(<ThemeProvider><Identity /></ThemeProvider>)
    const mark = screen.getByRole('img', { name: 'Gideon' })
    expect(mark).toHaveAttribute('src', gideonAssets.dark.mark)
    expect(mark).toHaveAttribute('width', '48')
    expect(activeFavicon()).toBe(gideonAssets.dark.favicon)
    fireEvent.click(screen.getByText('Toggle'))
    expect(mark).toHaveAttribute('src', gideonAssets.light.mark)
    expect(activeFavicon()).toBe(gideonAssets.light.favicon)
    expect(document.documentElement.dataset.mode).toBe('light')
    expect(localStorage.getItem('mode')).toBe('light')
    fireEvent.click(screen.getByText('Toggle'))
    expect(mark).toHaveAttribute('src', gideonAssets.dark.mark)
    expect(document.querySelectorAll('[data-gideon-appearance]')).toHaveLength(1)
  })

  it('restores a saved light preference and responds to another window changing it', () => {
    localStorage.setItem('mode', 'light')
    const { unmount } = render(<ThemeProvider><Identity /></ThemeProvider>)
    expect(screen.getByRole('img', { name: 'Gideon' })).toHaveAttribute('src', gideonAssets.light.mark)
    act(() => window.dispatchEvent(new StorageEvent('storage', { key: 'mode', newValue: 'dark' })))
    expect(screen.getByRole('img', { name: 'Gideon' })).toHaveAttribute('src', gideonAssets.dark.mark)
    unmount()
    expect(activeFavicon()).toBeUndefined()
  })

  it('lets a non-Gideon personality own the favicon and restores the themed logo on return', async () => {
    render(<ThemeProvider><Identity /></ThemeProvider>)
    const identity = document.querySelector<HTMLLinkElement>('[data-identity-test]')!
    identity.setAttribute('href', '/icons/personality-retro-terminal.svg')
    await waitFor(() => expect(activeFavicon()).toBeUndefined())
    fireEvent.click(screen.getByText('Toggle'))
    expect(activeFavicon()).toBeUndefined()
    identity.setAttribute('href', '/icons/personality-gideon-arcade.svg')
    await waitFor(() => expect(activeFavicon()).toBe(gideonAssets.light.favicon))
    identity.setAttribute('href', '/gideon.svg')
    await waitFor(() => expect(activeFavicon()).toBe(gideonAssets.light.favicon))
  })

  it('maps automatic appearance to each resolved system mode', () => {
    let state = { preference: 'auto' as const, system: 'dark' as const }
    expect(gideonAssets[resolveMode(state)].mark).toBe('/icons/gideon-dark.png')
    const next = themeReducer(state, { type: 'system', value: 'light' })
    expect(gideonAssets[resolveMode(next)].mark).toBe('/icons/gideon-light.png')
    render(<ThemeProvider><Identity /></ThemeProvider>)
    fireEvent.click(screen.getByText('Auto'))
    expect(localStorage.getItem('mode')).toBe('auto')
    expect(screen.getByRole('img', { name: 'Gideon' })).toHaveAttribute('src', gideonAssets.dark.mark)
  })
})
