import { describe, it, expect } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { Toaster } from './Toaster'
import { Button } from './Button'

// region cannot carry both urgencies, and an error queued behind three confirmations is the

describe('toasts are announced', () => {
  function fire(message: string, level: 'info' | 'success' | 'error') {
    act(() => {
      window.dispatchEvent(new CustomEvent('ne:toast', { detail: { message, level } }))
    })
  }

  it('mounts both live regions BEFORE any toast exists', () => {
    const { container } = render(<Toaster />)
    expect(container.querySelector('[role="status"][aria-live="polite"]')).not.toBeNull()
    expect(container.querySelector('[role="alert"][aria-live="assertive"]')).not.toBeNull()
  })

  it('routes a success to the POLITE region and an error to the ASSERTIVE one', () => {
    const { container } = render(<Toaster />)
    fire('Project deleted', 'success')
    fire('Upload failed', 'error')
    const polite = container.querySelector('[aria-live="polite"]')!
    const assertive = container.querySelector('[aria-live="assertive"]')!
    expect(polite.textContent).toContain('Project deleted')
    expect(polite.textContent, 'an error must not wait politely').not.toContain('Upload failed')
    expect(assertive.textContent).toContain('Upload failed')
    expect(assertive.textContent, 'a confirmation must not interrupt').not.toContain('Project deleted')
  })

  it('announces only ADDITIONS, so the auto-dismiss does not re-announce', () => {
    const { container } = render(<Toaster />)
    for (const r of container.querySelectorAll('[aria-live]')) {
      expect(r.getAttribute('aria-relevant')).toBe('additions')
    }
  })

  it('does not put the message in the tree twice', () => {
    const { container } = render(<Toaster />)
    fire('Saved the thing', 'success')
    const visibleText = [...container.querySelectorAll('[data-type="body-m"]')]
    expect(visibleText.length).toBeGreaterThan(0)
    for (const el of visibleText) expect(el.getAttribute('aria-hidden')).toBe('true')
  })

  it('keeps the CARD itself exposed — hiding it buries the Dismiss button', () => {
    const { container } = render(<Toaster />)
    fire('Saved', 'success')
    const btn = screen.getByRole('button', { name: /^Dismiss/ })
    for (let el = btn.parentElement; el && el !== container; el = el.parentElement) {
      expect(
        el.getAttribute('aria-hidden'),
        'no ancestor of the Dismiss button may be aria-hidden',
      ).not.toBe('true')
    }
  })

  it('names each Dismiss by its message — toasts stack up to 4', () => {
    render(<Toaster />)
    fire('Project deleted', 'success')
    fire('Trigger saved', 'success')
    const names = screen.getAllByRole('button', { name: /^Dismiss/ }).map((b) => b.getAttribute('aria-label'))
    expect(names).toEqual(['Dismiss: Project deleted', 'Dismiss: Trigger saved'])
    expect(new Set(names).size, 'each Dismiss must be distinguishable').toBe(names.length)
  })
})

describe('an in-flight button says it is busy', () => {
  it('sets aria-busy while loading', () => {
    render(<Button loading>Create project</Button>)
    expect(screen.getByRole('button').getAttribute('aria-busy')).toBe('true')
  })

  it('does NOT set it when idle', () => {
    render(<Button>Create project</Button>)
    expect(screen.getByRole('button').getAttribute('aria-busy')).toBeNull()
  })

  it('keeps its accessible name while busy', () => {
    render(<Button loading>Create project</Button>)
    expect(screen.getByRole('button', { name: /Create project/ })).toBeTruthy()
  })
})


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

describe('the hand-rolled in-flight population is pinned', () => {
  it('has not grown', () => {
    const hand = walk(SRC).flatMap((abs) => {
      const text = readFileSync(abs, 'utf8')
      return [...text.matchAll(/\{\s*(?:busy|saving|checking|applying|running|submitting|pending)\s*\?\s*<Loader2/g)]
        .map(() => abs.slice(SRC.length + 1))
    })
    expect(
      hand.length,
      `${hand.length} hand-rolled in-flight spinners (was 24). Prefer <Button loading={…}>, ` +
        'which carries aria-busy for free:\n  ' + [...new Set(hand)].join('\n  '),
    ).toBeLessThanOrEqual(24)
  })
})
