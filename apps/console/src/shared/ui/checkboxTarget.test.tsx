import { fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { Checkbox } from './forms'


const tokens = readFileSync(join(process.cwd(), 'src/shared/theme/tokens.css'), 'utf8')
const checkboxRule = tokens.match(/input\[type="checkbox"\]\s*\{[^}]*\}/s)?.[0] ?? ''

describe('Checkbox hit target', () => {
  it('sets a 16px minimum in the shared primitive and the global checkbox rule', () => {
    expect(checkboxRule).toMatch(/min-width:\s*16px/)
    expect(checkboxRule).toMatch(/min-height:\s*16px/)

    render(<Checkbox checked={false} onChange={() => {}} ariaLabel="Select item" />)
    expect(screen.getByRole('checkbox').className).toMatch(/\bmin-h-4\b/)
    expect(screen.getByRole('checkbox').className).toMatch(/\bmin-w-4\b/)
  })

  it('keeps the minimum and click isolation when nested in clickable content', () => {
    const onParentClick = vi.fn()
    const onChange = vi.fn()
    const style = document.createElement('style')
    style.textContent = checkboxRule
    document.head.append(style)

    const { unmount } = render(
      <div onClick={onParentClick}>
        <span>
          <span>
            <Checkbox checked={false} onChange={onChange} ariaLabel="Select nested item" />
          </span>
        </span>
      </div>,
    )
    const checkbox = screen.getByRole('checkbox', { name: 'Select nested item' })

    expect(getComputedStyle(checkbox).minWidth).toBe('16px')
    expect(getComputedStyle(checkbox).minHeight).toBe('16px')
    fireEvent.click(checkbox)
    expect(onChange).toHaveBeenCalledWith(true)
    expect(onParentClick).not.toHaveBeenCalled()

    unmount()
    style.remove()
  })
})
