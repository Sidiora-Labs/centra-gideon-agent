import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { AddItemButton } from './AddItemButton'


function classOf(el: HTMLElement | null): Set<string> {
  return new Set((el?.className ?? '').trim().split(/\s+/).filter(Boolean))
}

describe('AddItemButton', () => {
  it('is the medium-radius, container-filled, ink-var quiet add affordance', () => {
    const { getByRole } = render(<AddItemButton>Add step</AddItemButton>)
    const have = classOf(getByRole('button'))
    for (const t of ['inline-flex', 'items-center', 'gap-xs', 'rounded-md',
      'bg-surface-container', 'px-m', 'h-9', 'text-on-surface-var',
      'hover:bg-surface-high', 'transition-colors']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
    expect(getByRole('button').getAttribute('data-type')).toBe('body-s')
  })

  it('renders its children (the caller-owned leading glyph + label)', () => {
    const { getByText } = render(<AddItemButton>Add a workflow</AddItemButton>)
    expect(getByText('Add a workflow')).toBeInTheDocument()
  })

  it('merges an extra className (e.g. self-start) without dropping base chrome', () => {
    const { getByRole } = render(<AddItemButton className="self-start">x</AddItemButton>)
    const have = classOf(getByRole('button'))
    expect(have).toContain('self-start')
    expect(have).toContain('bg-surface-container')
    expect(have).toContain('h-9')
  })
})
