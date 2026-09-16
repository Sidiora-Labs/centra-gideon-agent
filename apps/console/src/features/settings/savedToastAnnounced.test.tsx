import { describe, expect, it, vi } from 'vitest'
import { render, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SavedToast, ToggleRow, NumberRow } from './settingsUI'


const SRC = join(process.cwd(), "src")
const settingsUI = () => readFileSync(join(SRC, 'features/settings/settingsUI.tsx'), 'utf8')

describe('SavedToast announces the save', () => {
  it('renders a polite status region that is EMPTY at rest', () => {
    const { container } = render(<SavedToast show={false} />)
    const region = container.querySelector('[role="status"]')
    expect(region, 'the region must exist even when nothing has been saved').not.toBeNull()
    expect(region!.getAttribute('aria-live')).toBe('polite')
    expect(region!.textContent).toBe('')
    expect(region!.className).toContain('sr-only')
  })

  it('carries the confirmation text once shown', () => {
    render(<SavedToast show={true} />)
    const region = document.querySelector('[role="status"]')!
    expect(region.textContent).toBe('Saved')
  })

  it('does not announce the confirmation twice', () => {
    const { container } = render(<SavedToast show={true} />)
    const visual = [...container.querySelectorAll('span')].find((s) => s.textContent?.includes('✓'))
    expect(visual, 'the visual confirmation must still render for sighted users').toBeTruthy()
    expect(visual!.getAttribute('aria-hidden')).toBe('true')
    expect(container.querySelectorAll('[role="status"]').length).toBe(1)
  })

  it('the region is mounted before the flash, in the real ToggleRow path', () => {
    const { container } = render(
      <ToggleRow label="LAN discovery" cfg={{ discovery_enabled: false }} field="discovery_enabled"
        patch={() => {}} />,
    )
    const region = container.querySelector('[role="status"]')
    expect(region, 'ToggleRow must mount the region before anything is saved').not.toBeNull()
    expect(region!.textContent).toBe('')
  })

  it('NumberRow inherits it too — the other shared config control', () => {
    const { container } = render(
      <NumberRow label="Retention" cfg={{ days: 90 }} field="days" min={1} max={365} patch={() => {}} />,
    )
    expect(container.querySelector('[role="status"]')).not.toBeNull()
  })

  it('a real save through ToggleRow announces, then falls silent', () => {
    vi.useFakeTimers()
    try {
      let onSaved: (() => void) | undefined
      const { container } = render(
        <ToggleRow label="LAN discovery" cfg={{ discovery_enabled: false }} field="discovery_enabled"
          patch={(_k, _v, cb) => { onSaved = cb as () => void }} />,
      )
      const toggle = container.querySelector('button')!
      act(() => { toggle.click() })
      expect(onSaved, 'ToggleRow must hand the row its flash callback').toBeTypeOf('function')
      act(() => { onSaved!() })
      expect(document.querySelector('[role="status"]')!.textContent).toBe('Saved')
      act(() => { vi.advanceTimersByTime(1600) })
      expect(document.querySelector('[role="status"]')!.textContent).toBe('')
    } finally {
      vi.useRealTimers()
    }
  })

  it('the pre-fix shape does not come back', () => {
    const src = settingsUI()
    expect(src).toMatch(/<span role="status" aria-live="polite" className="sr-only">\{show \? 'Saved' : ''\}<\/span>/)
    expect(/<motion\.span initial=\{\{ opacity: 0, scale: 0\.8, y: 2 \}\}[^>]*aria-live/.test(src),
      'aria-live on the animated span would mount with its content and not be observed').toBe(false)
  })
})
