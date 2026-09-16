import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'


vi.mock('../../shared/data/api', () => ({
  api: new Proxy({}, { get: () => () => Promise.resolve(null) }),
}))

import { ThemeProvider, DEFAULT_PREFERENCE } from '../../app/shell/theme'
import { AppearanceProvider } from '../../app/shell/appearance'
import { PersonalityProvider } from '../../app/shell/personality'
import { DesignPanel } from './DesignPanel'

function mount() {
  return render(
    <ThemeProvider>
      <AppearanceProvider>
        <PersonalityProvider>
          <DesignPanel />
        </PersonalityProvider>
      </AppearanceProvider>
    </ThemeProvider>,
  )
}

describe('Design reset covers the mode preference (#675)', () => {
  it('after switching to Light, Reset everything returns mode to the default', async () => {
    localStorage.clear()
    mount()
    await act(async () => { fireEvent.click(screen.getByLabelText('Mode: Light')) })
    expect(localStorage.getItem('mode')).toBe('light')
    expect(document.documentElement.classList.contains('light')).toBe(true)

    await act(async () => {
      fireEvent.click(screen.getByText('Reset everything to defaults'))
    })
    expect(localStorage.getItem('mode')).toBe(DEFAULT_PREFERENCE)
    expect(document.documentElement.classList.contains('light')).toBe(false)
  })

  it('the first-load fallback and the reset share one named default', async () => {
    localStorage.clear()
    mount()
    expect(localStorage.getItem('mode')).toBe(DEFAULT_PREFERENCE)
  })
})
