import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { QuietButton } from './QuietButton'
import { TileButton } from './TileButton'
import { AddItemButton } from './AddItemButton'


async function pressScaleOf(el: HTMLElement): Promise<number | null> {
  await act(async () => { fireEvent.pointerDown(el, { button: 0, isPrimary: true }) })
  await act(async () => { await new Promise((r) => setTimeout(r, 120)) })
  const m = /scale\(([\d.]+)\)/.exec(el.getAttribute('style') ?? '')
  return m ? Number(m[1]) : null
}

describe('QuietButton has a disabled state at all — the tier had none', () => {
  it('announces aria-disabled and NEVER the native attribute', () => {
    render(<QuietButton onClick={vi.fn()} disabled disabledReason="A hand-over is already in flight">Choose files</QuietButton>)
    const el = screen.getByRole('button', { name: 'Choose files' })
    expect(el.getAttribute('aria-disabled')).toBe('true')
    expect(el.hasAttribute('disabled'), 'the native attribute silences the control').toBe(false)
  })

  it('keeps its tab stop, so a keyboard user can reach it and hear why', () => {
    render(<QuietButton onClick={vi.fn()} disabled disabledReason="A hand-over is already in flight">Choose files</QuietButton>)
    const el = screen.getByRole('button', { name: 'Choose files' })
    el.focus()
    expect(document.activeElement, 'an unavailable control a keyboard cannot reach explains nothing').toBe(el)
  })

  it('carries the reason in its tooltip, after any caller title', () => {
    render(
      <QuietButton onClick={vi.fn()} title="Choose files to hand to this run" disabled disabledReason="A hand-over is already in flight">
        Choose files
      </QuietButton>,
    )
    const el = screen.getByRole('button', { name: 'Choose files' })
    expect(el.getAttribute('title')).toBe('Choose files to hand to this run — A hand-over is already in flight')
    expect(el.textContent).toBe('Choose files')
  })

  it('composes a bare reason when the caller passed no title', () => {
    render(<QuietButton onClick={vi.fn()} disabled disabledReason="Only the owner can do this">Delete</QuietButton>)
    expect(screen.getByRole('button', { name: 'Delete' }).getAttribute('title')).toBe('Only the owner can do this')
  })

  it('does not fire onClick or onDoubleClick', () => {
    const onClick = vi.fn()
    const onDoubleClick = vi.fn()
    render(<QuietButton onClick={onClick} onDoubleClick={onDoubleClick} disabled disabledReason="Busy">Choose files</QuietButton>)
    const el = screen.getByRole('button', { name: 'Choose files' })
    el.click()
    fireEvent.doubleClick(el)
    expect(onClick, 'a lit button whose click does nothing is a dead click').not.toHaveBeenCalled()
    expect(onDoubleClick).not.toHaveBeenCalled()
  })

  it('dims to the established 40 and shows the not-allowed cursor', () => {
    render(<QuietButton onClick={vi.fn()} disabled disabledReason="Busy">Choose files</QuietButton>)
    const cls = screen.getByRole('button', { name: 'Choose files' }).className
    expect(cls).toMatch(/\bopacity-40\b/)
    expect(cls).toMatch(/\bcursor-not-allowed\b/)
    expect(cls, 'a disabled control must not brighten on hover').not.toMatch(/hover:text-on-surface\b/)
  })

  it('a NOT-disabled QuietButton is untouched — the fix is opt-in', () => {
    const onClick = vi.fn()
    render(<QuietButton onClick={onClick} title="Download the findings log">Download</QuietButton>)
    const el = screen.getByRole('button', { name: 'Download' })
    expect(el.hasAttribute('aria-disabled')).toBe(false)
    expect(el.getAttribute('title')).toBe('Download the findings log')
    expect(el.className).not.toMatch(/opacity-40/)
    el.click()
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('a reason without `disabled` changes nothing', () => {
    render(<QuietButton onClick={vi.fn()} title="Download" disabledReason="Busy">Download</QuietButton>)
    const el = screen.getByRole('button', { name: 'Download' })
    expect(el.getAttribute('title')).toBe('Download')
    expect(el.hasAttribute('aria-disabled')).toBe(false)
  })
})

describe('all three tiers acknowledge a press', () => {
  const cases: [string, () => void][] = [
    ['QuietButton', () => { render(<QuietButton onClick={vi.fn()}>Download</QuietButton>) }],
    ['TileButton', () => { render(<TileButton onClick={vi.fn()} ariaLabel="Download">tile body</TileButton>) }],
    ['AddItemButton', () => { render(<AddItemButton onClick={vi.fn()}>Download</AddItemButton>) }],
  ]

  for (const [name, mount] of cases) {
    it(`${name} springs in on pointer-down`, async () => {
      mount()
      const scale = await pressScaleOf(screen.getByRole('button', { name: 'Download' }))
      expect(scale, `${name} writes no press transform at all`).not.toBeNull()
      expect(scale, `${name} must press IN`).toBeLessThan(1)
      expect(scale!, `${name} presses too deep to read as a button`).toBeGreaterThan(0.9)
      expect(scale!, `${name} presses too shallow to be perceptible`).toBeLessThan(0.995)
    })
  }

  it('a DISABLED QuietButton does not spring — inert must look inert', async () => {
    render(<QuietButton onClick={vi.fn()} disabled disabledReason="Busy">Download</QuietButton>)
    expect(await pressScaleOf(screen.getByRole('button', { name: 'Download' }))).toBeNull()
  })
})

describe('the two call sites whose gate was invisible', () => {
  const SRC = join(process.cwd(), "src")
  const code = (rel: string) =>
    readFileSync(join(SRC, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('OutboxPanel gates "Choose files" on the same flag as the input it forwards to', () => {
    const src = code('features/workflows/OutboxPanel.tsx')
    expect(src, 'the QuietButton must carry the gate').toMatch(/<QuietButton[\s\S]{0,400}?disabled=\{dropBusy\}/)
    expect(src, 'the reason must be announced, not just the state')
      .toMatch(/<QuietButton[\s\S]{0,400}?disabledReason="A hand-over is already in flight"/)
    expect(src, 'the input keeps its own gate').toMatch(/type="file"[\s\S]{0,300}?disabled=\{dropBusy\}/)
  })

  it('WorkflowDefDetail gates "Refine now" on the same flag its JS guard reads', () => {
    const src = code('features/workflows/WorkflowDefDetail.tsx')
    expect(src).toMatch(/if \(refining\) return/)
    expect(src).toMatch(/<QuietButton[\s\S]{0,400}?disabled=\{refining\}/)
    expect(src).toMatch(/<QuietButton[\s\S]{0,400}?disabledReason="A refinement is already in flight"/)
  })

  it('the comment-stripping is load-bearing, not decorative', () => {
    const raw = readFileSync(join(SRC, 'features/workflows/OutboxPanel.tsx'), 'utf8')
    expect(raw).toMatch(/disabled=\{dropBusy\}/)
    expect(raw.length, 'the file must actually carry comments').toBeGreaterThan(
      code('features/workflows/OutboxPanel.tsx').length,
    )
  })
})

describe('the three tiers spell spacing in tokens, not Tailwind defaults', () => {
  const UI = join(process.cwd(), "src/shared/ui")
  const body = (f: string) =>
    readFileSync(join(UI, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('QuietButton uses gap-xs / px-s', () => {
    const src = body('QuietButton.tsx')
    expect(src).toMatch(/\bgap-xs\b/)
    expect(src).toMatch(/\bpx-s\b/)
    expect(src).not.toMatch(/\bgap-1(\.5)?\b/)
    expect(src).not.toMatch(/\bpx-2\b/)
  })

  it('AddItemButton uses gap-xs', () => {
    const src = body('AddItemButton.tsx')
    expect(src).toMatch(/\bgap-xs\b/)
    expect(src).not.toMatch(/\bgap-1(\.5)?\b/)
  })
})
