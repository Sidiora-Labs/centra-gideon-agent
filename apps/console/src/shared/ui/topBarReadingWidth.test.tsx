import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { TopBar } from './TopBar'

it('keeps a chat header readable without hiding its primary action', async () => {
  const back = vi.fn()
  const create = vi.fn()
  const { container } = render(<TopBar contentWidth={1120}
    left={<button type="button" onClick={back}>Conversation</button>}
    right={<button type="button" onClick={create}>New chat</button>} />)
  const header = container.querySelector('header')!
  expect(header.style.paddingInlineStart).toContain('1120px')
  expect(header.style.paddingInlineEnd).toContain('1120px')
  expect(header.querySelector('[data-header-right]')).not.toHaveAttribute('inert')
  const user = userEvent.setup()
  screen.getByRole('button', { name: 'Conversation' }).focus()
  await user.tab()
  expect(screen.getByRole('button', { name: 'New chat' })).toHaveFocus()
  await user.keyboard('{Enter}')
  expect(create).toHaveBeenCalledOnce()
  await user.click(screen.getByRole('button', { name: 'Conversation' }))
  expect(back).toHaveBeenCalledOnce()
})

it('leaves wide pages on the normal shell edge alignment', () => {
  const { container } = render(<TopBar left={<span>Library</span>} right={<button type="button">Import</button>} />)
  const header = container.querySelector('header')!
  expect(header.style.paddingInlineStart).not.toContain('1120px')
  expect(header.style.paddingInlineEnd).not.toContain('1120px')
  expect(screen.getByRole('button', { name: 'Import' })).toBeVisible()
})
