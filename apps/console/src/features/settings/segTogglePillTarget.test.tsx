import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SegToggle } from './bento'


describe('SegToggle pills are 24px targets', () => {
  const OPTS = [{ key: 'light', label: 'Light' }, { key: 'dark', label: 'Dark' }, { key: 'auto', label: 'Auto' }]

  it('is 24px tall', () => {
    render(<SegToggle value="dark" options={OPTS} onPick={vi.fn()} ariaLabel="Mode" />)
    expect(screen.getByRole('button', { name: 'Mode: Light' }).className).toMatch(/\bh-6\b/)
  })

  it('hands the 2px back, so the group keeps its height', () => {
    render(<SegToggle value="dark" options={OPTS} onPick={vi.fn()} ariaLabel="Mode" />)
    expect(screen.getByRole('button', { name: 'Mode: Dark' }).className).toMatch(/-my-px/)
  })

  it('no longer carries the off-ramp height', () => {
    const src = readFileSync(join(process.cwd(), "src/features/settings/bento.tsx"), 'utf8')
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code, 'the 22px pill height must be gone from the component').not.toMatch(/h-\[22px\][^"]*text-\[0\.75rem\] transition-colors/)
  })

  it('keeps its exclusive-choice semantics', () => {
    render(<SegToggle value="dark" options={OPTS} onPick={vi.fn()} ariaLabel="Mode" />)
    expect(screen.getByRole('button', { name: 'Mode: Dark' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'Mode: Light' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('still ignores a click on the already-selected option', async () => {
    const onPick = vi.fn()
    render(<SegToggle value="dark" options={OPTS} onPick={onPick} ariaLabel="Mode" />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Mode: Dark' })) })
    expect(onPick, 'picking the current value is a no-op').not.toHaveBeenCalled()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Mode: Auto' })) })
    expect(onPick).toHaveBeenCalledWith('auto')
  })

  it('has enough adopters that fixing the component was the right move', () => {
    const widgets = readFileSync(join(process.cwd(), "src/features/settings/settingsWidgets.tsx"), 'utf8')
    const uses = (widgets.match(/<SegToggle\b/g) ?? []).length
    expect(uses, 'Mode, Density, Min severity, …').toBeGreaterThanOrEqual(3)
  })
})
