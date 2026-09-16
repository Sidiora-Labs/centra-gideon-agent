import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { AssistantActions } from '../../features/chat/MessageActions'


describe('a pager arrow at its limit keeps its tab stop', () => {
  const props = {
    text: 'an answer', isLast: true, variantCount: 3,
    onCopy: vi.fn(), onRegenerate: vi.fn(), onFork: vi.fn(), onSpeak: vi.fn(), onSwitchVariant: vi.fn(),
  }

  it('names the limit instead of going silent at the first answer', () => {
    render(<AssistantActions {...props} variantIdx={0} />)
    const prev = screen.getByRole('button', { name: 'Previous answer' })
    expect(prev.hasAttribute('disabled'), 'the native attribute would remove the tab stop').toBe(false)
    expect(prev.getAttribute('aria-disabled')).toBe('true')
    expect(prev.getAttribute('title')).toBe('Previous answer — Already at the first answer')
  })

  it('names the limit at the last answer', () => {
    render(<AssistantActions {...props} variantIdx={2} />)
    const next = screen.getByRole('button', { name: 'Next answer' })
    expect(next.hasAttribute('disabled')).toBe(false)
    expect(next.getAttribute('aria-disabled')).toBe('true')
    expect(next.getAttribute('title')).toBe('Next answer — Already at the last answer')
  })

  it('still refuses the click at the limit', () => {
    const onSwitchVariant = vi.fn()
    render(<AssistantActions {...props} variantIdx={0} onSwitchVariant={onSwitchVariant} />)
    fireEvent.click(screen.getByRole('button', { name: 'Previous answer' }))
    expect(onSwitchVariant, 'aria-disabled is advisory — the click must be refused in code').not.toHaveBeenCalled()
  })

  it('STAYS IN THE TAB ORDER when the press reaches the limit', () => {
    const { rerender } = render(<AssistantActions {...props} variantIdx={1} />)
    const prev = screen.getByRole('button', { name: 'Previous answer' })
    prev.focus()
    expect(document.activeElement).toBe(prev)
    fireEvent.click(prev)
    rerender(<AssistantActions {...props} variantIdx={0} />)
    const clamped = screen.getByRole('button', { name: 'Previous answer' })
    expect(clamped.hasAttribute('disabled'), 'the native attribute is what evicts it from the tab order').toBe(false)
    expect(clamped.getAttribute('aria-disabled')).toBe('true')
  })

  it('leaves a mid-sequence arrow completely alone', () => {
    render(<AssistantActions {...props} variantIdx={1} />)
    for (const name of ['Previous answer', 'Next answer']) {
      const b = screen.getByRole('button', { name })
      expect(b.getAttribute('aria-disabled'), `${name} is live at 2/3`).toBe(null)
      expect(b.getAttribute('title')).toBe(name)
    }
  })
})


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const BOUNDARY = /\b(?:atStart|atEnd)\b|\b(?:index|idx|i)\s*(?:===\s*0|<=\s*0)|===\s*(?:\w+\.)?(?:length|count|total)\s*-\s*1|\bparent\s*===\s*path\b/

function boundaryGated(): Array<{ file: string; line: number; tag: string; src: string }> {
  const out: Array<{ file: string; line: number; tag: string; src: string }> = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    for (const m of text.matchAll(/<(?:Button|button|motion\.button)\b/g)) {
      let depth = 0
      for (let i = m.index! + m[0].length; i < text.length; i++) {
        const ch = text[i]
        if (ch === '{') depth++
        else if (ch === '}') depth--
        else if (ch === '>' && depth === 0) {
          const tag = text.slice(m.index!, i + 1)
          const gate = /(?<!aria-)disabled=\{([\s\S]*?)\}|unavailableWhen\(([\s\S]*?),/.exec(tag)
          if (gate && BOUNDARY.test(gate[1] ?? gate[2] ?? '')) {
            out.push({
              file: abs.slice(SRC.length + 1),
              line: text.slice(0, m.index).split('\n').length,
              tag,
              src: text,
            })
          }
          break
        }
      }
    }
  }
  return out
}

describe('no control goes silently dead at its limit', () => {
  const gated = boundaryGated()

  it('finds the boundary-gated controls (not vacuously green)', () => {
    expect(gated.length, 'the matcher must find position-boundary gates').toBeGreaterThanOrEqual(4)
  })

  it('has NO unexplained boundary-gated control', () => {
    const unexplained = gated.filter((t) => !/disabledReason|unavailableWhen\(/.test(t.tag))
    expect(
      unexplained.length,
      `${unexplained.length} control(s) go dead at a boundary without naming the limit, and drop ` +
        'out of the tab order under the user who pressed them. Pass `disabledReason` on a ' +
        '<Button>, or spread `unavailableWhen()` on a raw <button>:\n  ' +
        unexplained.map((t) => `${t.file}:${t.line}`).join('\n  '),
    ).toBe(0)
  })

  it('restates the dim on aria-disabled wherever it left the native attribute', () => {
    const missing = gated
      .filter((t) => /unavailableWhen\(/.test(t.tag))
      .filter((t) => !/aria-disabled:opacity-\d+/.test(t.src))
    expect(
      missing.length,
      'converted control(s) never restate their dim on `aria-disabled:`, so they render as live:\n  ' +
        missing.map((t) => `${t.file}:${t.line}`).join('\n  '),
    ).toBe(0)
  })
})


describe('a boundary-gated icon button names its limit', () => {
  const REORDER = [
    'features/settings/ModelsPanel.tsx',
    'features/code/CodePlanReview.tsx',
    'features/loops/LoopPlanReview.tsx',
  ]

  const iconButtonTags = (text: string) => {
    const out: string[] = []
    for (const m of text.matchAll(/<(?:IconButton|SquareIconButton)\b/g)) {
      let depth = 0
      for (let i = m.index! + m[0].length; i < text.length; i++) {
        const ch = text[i]
        if (ch === '{') depth++
        else if (ch === '}') depth--
        else if (ch === '>' && depth === 0) { out.push(text.slice(m.index!, i + 1)); break }
      }
    }
    return out
  }

  it.each(REORDER)('%s explains every boundary-gated reorder control', (rel) => {
    const src = readFileSync(join(SRC, rel), 'utf8')
    const gated = iconButtonTags(src).filter((t) => {
      const g = /(?<!aria-)disabled=\{([\s\S]*?)\}/.exec(t)
      return g && BOUNDARY.test(g[1])
    })
    expect(gated.length, `${rel} must still have its reorder pair`).toBe(2)
    for (const tag of gated) {
      expect(tag, `a boundary-gated icon button in ${rel} says nothing on arrival`).toMatch(/disabledReason/)
    }
  })

  it('keeps the reason on the BOUNDARY branch of a compound gate', () => {
    const src = readFileSync(join(SRC, 'features/settings/ModelsPanel.tsx'), 'utf8')
    const boundaryGated = iconButtonTags(src).filter((tag) => {
      const g = /(?<!aria-)disabled=\{([\s\S]*?)\}/.exec(tag)
      return !!g && BOUNDARY.test(g[1])
    })
    expect(boundaryGated.length, 'the boundary-gated reorder controls must still be found').toBeGreaterThanOrEqual(2)
    for (const tag of boundaryGated) {
      const m = /disabledReason=\{([^}]*)\}/.exec(tag)
      expect(m?.[1], 'a boundary-gated control must carry a reason at all').toBeTruthy()
      expect(
        m![1],
        'the reason must be conditional on the boundary term, not stated flatly',
      ).toMatch(/\?/)
    }
  })
})
