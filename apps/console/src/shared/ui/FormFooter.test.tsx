import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { FormFooter } from './FormFooter'


function classOf(el: HTMLElement | null): Set<string> {
  return new Set((el?.className ?? '').trim().split(/\s+/).filter(Boolean))
}

describe('FormFooter', () => {
  it('is the sticky, edge-bleeding, top-bordered, right-aligned action bar', () => {
    const { container } = render(<FormFooter><button>Save</button></FormFooter>)
    const have = classOf(container.firstElementChild as HTMLElement)
    for (const t of ['sticky', 'bottom-0', '-mx-l', 'px-l', 'py-3', 'bg-surface/95',
      'border-t', 'border-outline-variant/40', 'flex', 'justify-end', 'gap-s']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
  })

  it('renders its children (the caller-owned Cancel/Save buttons)', () => {
    const { getByText } = render(
      <FormFooter><button>Cancel</button><button>Save</button></FormFooter>,
    )
    expect(getByText('Cancel')).toBeInTheDocument()
    expect(getByText('Save')).toBeInTheDocument()
  })

  it('merges an extra className without dropping the base chrome', () => {
    const { container } = render(<FormFooter className="mt-2"><span /></FormFooter>)
    const have = classOf(container.firstElementChild as HTMLElement)
    expect(have).toContain('mt-2')
    expect(have).toContain('sticky')
    expect(have).toContain('justify-end')
  })
})
