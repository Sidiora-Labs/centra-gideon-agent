import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { Modal } from './Modal'

afterEach(cleanup)

const longName = 'integration-' + 'longunbrokenname'.repeat(24)

describe('shared modal on a narrow viewport', () => {
  it('keeps a tall app form and its controls reachable inside the capped scroll area', () => {
    const events: string[] = []
    render(<Modal title={`Install ${longName}`} onClose={() => events.push('close')}>
      <div style={{ minWidth: 420 }}>
        <p>{longName}</p>
        <label htmlFor="source">Source</label>
        <input id="source" />
        <div style={{ height: 1200 }} />
        <button onClick={() => events.push('install')}>Install</button>
      </div>
    </Modal>)

    const dialog = screen.getByRole('dialog', { name: `Install ${longName}` })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveClass('max-h-[calc(100dvh-1rem)]', 'min-w-0', 'max-w-full')
    expect(dialog.parentElement).toHaveClass('overflow-y-auto', 'p-s')
    const body = dialog.lastElementChild as HTMLElement
    expect(body).toHaveClass('overflow-x-auto', 'overflow-y-auto', 'min-w-0', 'break-words')
    expect(within(dialog).getByRole('textbox', { name: 'Source' })).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Install' }))
    expect(events).toEqual(['install'])
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(events).toEqual(['install', 'close'])
  })

  it('keeps the close button named and clickable alongside an unbroken title', () => {
    const events: string[] = []
    render(<Modal title={longName} onClose={() => events.push('close')}><p>File details</p></Modal>)
    const dialog = screen.getByRole('dialog', { name: longName })
    expect(dialog.querySelector('h2')).toHaveClass('min-w-0', 'break-words')
    const close = within(dialog.querySelector('header') as HTMLElement).getByRole('button')
    expect(close).toHaveAccessibleName()
    fireEvent.click(close)
    expect(events).toEqual(['close'])
  })
})
