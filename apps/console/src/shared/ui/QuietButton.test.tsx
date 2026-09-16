import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { QuietButton } from './QuietButton'


function classOf(el: HTMLElement | null): Set<string> {
  return new Set((el?.className ?? '').trim().split(/\s+/).filter(Boolean))
}

describe('QuietButton', () => {
  it('is the 28px, ink-low, medium-radius quiet toolbar action', () => {
    const { getByRole } = render(<QuietButton>Download</QuietButton>)
    const have = classOf(getByRole('button'))
    for (const t of ['inline-flex', 'items-center', 'gap-xs', 'rounded-md',
      'px-s', 'h-7', 'text-on-surface-low',
      'hover:bg-surface-high', 'hover:text-on-surface']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
    expect(getByRole('button').getAttribute('data-type')).toBe('caption')
  })

  it('renders its children (the caller-owned leading glyph + label)', () => {
    const { getByText } = render(<QuietButton>Source file</QuietButton>)
    expect(getByText('Source file')).toBeInTheDocument()
  })

  it('forwards title (the supplementary tooltip) to the button', () => {
    const { getByRole } = render(<QuietButton title="Download this artifact">Download</QuietButton>)
    expect(getByRole('button')).toHaveAttribute('title', 'Download this artifact')
  })

  it('merges an extra className without dropping base chrome', () => {
    const { getByRole } = render(<QuietButton className="ml-auto">x</QuietButton>)
    const have = classOf(getByRole('button'))
    expect(have).toContain('ml-auto')
    expect(have).toContain('h-7')
    expect(have).toContain('text-on-surface-low')
  })
})
