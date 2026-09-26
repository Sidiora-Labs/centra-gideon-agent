import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ShellCornerLeft } from './ShellCorners'

afterEach(() => vi.unstubAllGlobals())

describe('sidebar corner toggle', () => {
  it('keeps a visible navigation glyph and accurate name through immediate collapse changes', () => {
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      disconnect() {}
    })
    const onToggle = vi.fn()
    const view = render(<ShellCornerLeft collapsed={false} onToggle={onToggle} />)

    const expanded = screen.getByRole('button', { name: 'Collapse sidebar' })
    expect(expanded).toHaveAttribute('title', 'Collapse sidebar')
    expect(expanded.querySelectorAll('svg')).toHaveLength(1)
    expect(expanded.querySelector('svg')).toHaveClass('lucide-panel-left-close')
    fireEvent.click(expanded)
    expect(onToggle).toHaveBeenCalledOnce()

    view.rerender(<ShellCornerLeft collapsed onToggle={onToggle} />)
    const collapsed = screen.getByRole('button', { name: 'Expand sidebar' })
    expect(collapsed).toHaveAttribute('title', 'Expand sidebar')
    expect(collapsed.querySelectorAll('svg')).toHaveLength(1)
    expect(collapsed.querySelector('svg')).toHaveClass('lucide-panel-left-open')
    expect(collapsed.querySelector('span')).not.toHaveAttribute('style')

    view.rerender(<ShellCornerLeft collapsed={false} onToggle={onToggle} />)
    expect(screen.getByRole('button', { name: 'Collapse sidebar' }).querySelector('svg'))
      .toHaveClass('lucide-panel-left-close')
  })
})
