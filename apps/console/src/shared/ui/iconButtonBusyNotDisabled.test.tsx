import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { MotionConfig } from 'framer-motion'
import { Send, Wifi } from 'lucide-react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { IconButton } from './IconButton'
import { SquareIconButton } from './SquareIconButton'

// remaining `disabled` gates are real unavailability (a license gate, `disabled={false}`, an

describe('an icon button in flight says "working", not "unavailable"', () => {
  const cases = [
    ['IconButton', (p: Record<string, unknown>) => <IconButton icon={Send} label="Send steer" {...p} />],
    ['SquareIconButton', (p: Record<string, unknown>) => <SquareIconButton icon={Wifi} label="Test connection" {...p} />],
  ] as const

  for (const [name, Render] of cases) {
    describe(name, () => {
      it('announces aria-busy and NOT aria-disabled', () => {
        render(Render({ loading: true, onClick: () => {} }))
        const btn = screen.getByRole('button')
        expect(btn.getAttribute('aria-busy'), 'the state that says "working"').toBe('true')
        expect(btn.getAttribute('aria-disabled'), 'a busy control is not an unavailable one').toBe(null)
      })

      it('keeps its ink — no dim, no not-allowed cursor', () => {
        const { container } = render(Render({ loading: true, onClick: () => {} }))
        const cls = container.querySelector('button')!.className
        expect(cls, 'dimming is what made a working button read as dead').not.toMatch(/opacity-40/)
        expect(cls).not.toMatch(/cursor-not-allowed/)
        expect(cls, 'the cursor is the honest signal here').toMatch(/cursor-progress/)
      })

      it('swaps the glyph for a spinner, aria-hidden so the name never changes', () => {
        const { container } = render(Render({ loading: true, onClick: () => {} }))
        const spinner = container.querySelector('.animate-spin')
        expect(spinner, 'the glyph IS the button here — a busy one must look busy').toBeTruthy()
        expect(spinner!.closest('[aria-hidden]'), 'the spinner must not pollute the accessible name').toBeTruthy()
        expect(screen.getByRole('button').getAttribute('aria-label')).toBe(name === 'IconButton' ? 'Send steer' : 'Test connection')
      })

      it('REFUSES the second click', () => {
        const onClick = vi.fn()
        render(Render({ loading: true, onClick }))
        const btn = screen.getByRole('button')
        fireEvent.click(btn)
        fireEvent.click(btn)
        expect(onClick, 'an in-flight action must not be re-entrant').not.toHaveBeenCalled()
      })

      it('is live again the moment loading clears, with no lingering busy state', () => {
        const onClick = vi.fn()
        const { rerender } = render(Render({ loading: true, onClick }))
        rerender(Render({ loading: false, onClick }))
        const btn = screen.getByRole('button')
        expect(btn.getAttribute('aria-busy')).toBe(null)
        fireEvent.click(btn)
        expect(onClick).toHaveBeenCalledTimes(1)
      })

      it('still renders the busy state under reduced motion', () => {
        const { container } = render(
          <MotionConfig reducedMotion="always">{Render({ loading: true, onClick: () => {} })}</MotionConfig>,
        )
        expect(container.querySelector('.animate-spin'), 'reduce ≠ eliminate — the state indicator survives').toBeTruthy()
        expect(screen.getByRole('button').getAttribute('aria-busy')).toBe('true')
      })

      it('leaves a genuine gate alone — disabled still means unavailable', () => {
        const onClick = vi.fn()
        const { container } = render(Render({ disabled: true, onClick }))
        const btn = screen.getByRole('button')
        expect(btn.getAttribute('aria-disabled')).toBe('true')
        expect(btn.getAttribute('aria-busy'), 'a gate is not a spinner').toBe(null)
        expect(container.querySelector('.animate-spin')).toBeNull()
        expect(btn.className).toMatch(/opacity-40/)
        fireEvent.click(btn)
        expect(onClick).not.toHaveBeenCalled()
      })

      it('a compound gate carries both, and reads as unavailable-and-working', () => {
        const { container } = render(Render({ disabled: true, loading: true, onClick: () => {} }))
        const btn = screen.getByRole('button')
        expect(btn.getAttribute('aria-disabled')).toBe('true')
        expect(btn.getAttribute('aria-busy')).toBe('true')
        expect(container.querySelector('.animate-spin')).toBeTruthy()
      })
    })
  }
})


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

function iconButtonTags(text: string): Array<{ tag: string; line: number }> {
  const out: Array<{ tag: string; line: number }> = []
  for (const m of text.matchAll(/<(?:Square)?IconButton\b/g)) {
    let depth = 0
    for (let i = m.index! + m[0].length; i < text.length; i++) {
      const ch = text[i]
      if (ch === '{') depth++
      else if (ch === '}') depth--
      else if (ch === '>' && depth === 0) {
        out.push({ tag: text.slice(m.index!, i + 1), line: text.slice(0, m.index!).split('\n').length })
        break
      }
    }
  }
  return out
}

const IN_FLIGHT = /busy|saving|sending|testing|rechecking|reconnecting|deleting|pending|loading|installing|uploading|refreshing|syncing|retrying|submitting/i

const all = walk(SRC).flatMap((abs) =>
  iconButtonTags(readFileSync(abs, 'utf8')).map((t) => ({ ...t, file: abs.slice(SRC.length + 1) })),
)

describe('no icon button spells an in-flight state as `disabled`', () => {
  it('finds the population, and the adopters (not vacuously green)', () => {
    expect(all.length, 'the brace-aware matcher must find the icon buttons').toBeGreaterThanOrEqual(140)
    const adopters = all.filter((t) => /\bloading=\{/.test(t.tag))
    expect(adopters.length, 'the in-flight population must actually be on `loading`').toBeGreaterThanOrEqual(16)
  })

  it('has no `disabled` gate that is really a spinner', () => {
    const miscast = all.filter((t) => {
      const gate = /(?<!aria-)\bdisabled=\{([\s\S]*?)\}(?=\s|$|\/)/.exec(t.tag)?.[1]
      return gate !== undefined && IN_FLIGHT.test(gate)
    })
    expect(
      miscast.map((t) => `${t.file}:${t.line}`),
      'an in-flight icon button that passes `disabled` dims to 40%, says `cursor: not-allowed` and ' +
        'announces `aria-disabled` — it claims it cannot be used while it is being used. Pass ' +
        '`loading` instead, and split a compound gate: `disabled={!text.trim()} loading={busy}`.',
    ).toEqual([])
  })

  it('and no caller hand-rolls the spinner the primitive now owns', () => {
    const rolled = all.filter((t) => /Loader2/.test(t.tag) && /(?<!aria-)\bdisabled=\{/.test(t.tag))
    expect(
      rolled.map((t) => `${t.file}:${t.line}`),
      'the primitive owns the glyph cross-fade — pass `loading` and keep your idle glyph',
    ).toEqual([])
  })

  it('both primitives actually implement the state (the props are not decoration)', () => {
    for (const rel of ['shared/ui/IconButton.tsx', 'shared/ui/SquareIconButton.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, `${rel} must announce the state`).toContain('aria-busy={state.busy}')
      expect(src, `${rel} must gate the click on BOTH reasons`).toContain('controlAvailability(disabled, loading, disabledReason, true)')
      expect(src, `${rel} must refuse the click through that guard`).toContain('activateControl(event, state.blocked, onClick)')
      expect(src, `${rel} must not dim while merely busy`).not.toMatch(/loading[\s\S]{0,40}opacity-40/)
    }
  })
})
