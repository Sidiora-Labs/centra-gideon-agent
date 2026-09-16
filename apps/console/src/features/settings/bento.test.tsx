import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, act } from '@testing-library/react'
import { Switch } from './bento'


describe('bento Switch', () => {
  it('fires its toggle AND does not bubble to the card nav overlay', async () => {
    const onToggle = vi.fn()
    const onNav = vi.fn()
    const { getByRole } = render(
      <div onClick={onNav}>
        <Switch on={false} onToggle={onToggle} label="Send on Enter" />
      </div>,
    )
    await act(async () => { fireEvent.click(getByRole('switch')) })
    expect(onToggle).toHaveBeenCalledTimes(1)
    expect(onToggle).toHaveBeenCalledWith(true)
    expect(onNav).not.toHaveBeenCalled()
  })

  it('is a no-op when disabled', () => {
    const onToggle = vi.fn()
    const { getByRole } = render(<Switch on={false} onToggle={onToggle} label="x" disabled />)
    fireEvent.click(getByRole('switch'))
    expect(onToggle).not.toHaveBeenCalled()
  })
})
