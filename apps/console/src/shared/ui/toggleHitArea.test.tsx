import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Toggle } from './Toggle'


describe('the sm switch is a reachable target', () => {
  it('gives the button a 24px-tall hit box', () => {
    render(<Toggle size="sm" on={false} onChange={vi.fn()} label="Restore sessions" />)
    expect(screen.getByRole('switch').className).toMatch(/\bh-6\b/)
  })

  it('returns the extra height to the layout, so nothing moves', () => {
    render(<Toggle size="sm" on={false} onChange={vi.fn()} label="Restore sessions" />)
    expect(screen.getByRole('switch').className).toMatch(/-my-0\.5/)
  })

  it('keeps the 20px track as the painted visual, inside the button', () => {
    render(<Toggle size="sm" on={false} onChange={vi.fn()} label="Restore sessions" />)
    const track = screen.getByRole('switch').querySelector('span')!
    expect(track.className, 'the sm track stays h-5 w-9 — the fix is the hit box, not the design').toMatch(/h-5 w-9/)
  })

  it('does not pad md — it is already 24px and needs no correction', () => {
    render(<Toggle on={false} onChange={vi.fn()} label="Deliver notifications" />)
    const btn = screen.getByRole('switch')
    expect(btn.className).toMatch(/\bh-6\b/)
    expect(btn.className, 'md must not gain a negative margin it does not need').not.toMatch(/-my-0\.5/)
    expect(btn.querySelector('span')!.className).toMatch(/h-6 w-10/)
  })

  it('still toggles, and still announces its state and name', () => {
    const onChange = vi.fn()
    render(<Toggle size="sm" on onChange={onChange} label="Send on Enter" />)
    const btn = screen.getByRole('switch', { name: 'Send on Enter' })
    expect(btn.getAttribute('aria-checked')).toBe('true')
    btn.click()
    expect(onChange).toHaveBeenCalledWith(false)
  })

  it('leaves the read-only indicator alone — it is not a target', () => {
    render(<Toggle size="sm" on readOnly label="Enabled" />)
    const el = screen.getByRole('switch')
    expect(el.tagName).toBe('SPAN')
    expect(el.className).toMatch(/h-5 w-9/)
    expect(el.className).not.toMatch(/-my-0\.5/)
  })
})
