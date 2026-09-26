import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MobileComposer } from './mobile-composer'

describe('mobile composer embedded editor', () => {
  it('preserves the donor attachment and field controls by default', () => {
    const onAttach = vi.fn()
    const { container } = render(<MobileComposer value="draft" keyboardOpen={false} running={false}
      actions={[]} onAttach={onAttach} onSend={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add an attachment' }))
    expect(onAttach).toHaveBeenCalledOnce()
    expect(screen.getByRole('textbox', { name: 'Message' })).toBeInTheDocument()
    expect(container.querySelector('input')?.parentElement).toHaveClass('rounded-[18px]')
  })

  it('uses one caller-owned editor without duplicate attachment or field chrome', () => {
    const onSend = vi.fn()
    const { container } = render(<MobileComposer value="real draft" editor={<textarea aria-label="CodeMirror editor" />}
      keyboardOpen={false} running={false} actions={[]} onSend={onSend} showAttach={false} flat />)
    expect(screen.queryByRole('button', { name: 'Add an attachment' })).toBeNull()
    expect(screen.getAllByRole('textbox')).toHaveLength(1)
    expect(screen.getByRole('textbox', { name: 'CodeMirror editor' }).parentElement).not.toHaveClass('rounded-[18px]')
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(onSend).toHaveBeenCalledOnce()
    expect(container.querySelector('[data-slot="mobile-composer"]')).toBeInTheDocument()
  })
})
