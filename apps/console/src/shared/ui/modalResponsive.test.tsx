import { createRef, useEffect } from 'react'
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


describe('Modal controlled presentation lifecycle', () => {
  it('keeps closed runtime children mounted without owning focus or Escape, then restores its opener', () => {
    const opener = document.createElement('button')
    opener.textContent = 'Open voice controls'
    document.body.append(opener)
    opener.focus()
    const origin = { current: opener }
    const initial = createRef<HTMLInputElement>()
    const events: string[] = []
    function Runtime() {
      useEffect(() => { events.push('start'); return () => { events.push('stop') } }, [])
      return <input ref={initial} aria-label="Voice name" />
    }
    const view = (open: boolean) => <Modal title="Voice controls" open={open} keepMounted initialFocus={initial}
      restoreFocus={origin} dismissOnBackdrop={false} onClose={() => events.push('close')}><Runtime /></Modal>
    const { rerender, unmount } = render(view(false))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(initial.current?.isConnected).toBe(true)
    expect(events).toEqual(['start'])
    expect(document.activeElement).toBe(opener)
    expect(fireEvent.keyDown(window, { key: 'Escape' })).toBe(true)
    rerender(view(true))
    const dialog = screen.getByRole('dialog', { name: 'Voice controls' })
    expect(document.activeElement).toBe(initial.current)
    fireEvent.keyDown(initial.current!, { key: 'Tab' })
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Close' }))
    fireEvent.click(dialog.parentElement!.firstElementChild!)
    expect(events).toEqual(['start'])
    expect(fireEvent.keyDown(window, { key: 'Escape' })).toBe(false)
    expect(events).toEqual(['start', 'close'])
    rerender(view(false))
    expect(document.activeElement).toBe(opener)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(fireEvent.keyDown(opener, { key: 'Escape' })).toBe(true)
    expect(events).toEqual(['start', 'close'])
    rerender(view(true))
    expect(document.activeElement).toBe(initial.current)
    expect(events).toEqual(['start', 'close'])
    unmount()
    expect(document.activeElement).toBe(opener)
    expect(events).toEqual(['start', 'close', 'stop'])
    opener.remove()
  })

  it.each(['centered', 'drawer', 'bottom-sheet', 'fullscreen'] as const)('owns the actual %s presentation', (presentation) => {
    render(<Modal presentation={presentation} title="Details" onClose={() => {}}><p>Content</p></Modal>)
    const dialog = screen.getByRole('dialog', { name: 'Details' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog.parentElement!.parentElement).toBe(document.body)
    if (presentation === 'drawer') {
      expect(dialog.style.width).toBe('18rem')
      expect(dialog.style.maxWidth).toBe('85vw')
      expect(dialog.parentElement).toHaveClass('justify-end')
    } else if (presentation === 'bottom-sheet') {
      expect(dialog.style.maxWidth).toBe('40rem')
      expect(dialog).toHaveClass('max-h-[80dvh]')
      expect(dialog.parentElement).toHaveClass('items-end')
    } else if (presentation === 'fullscreen') {
      expect(dialog).toHaveClass('h-full', 'max-h-[100dvh]')
      expect(dialog.style.maxWidth).toBe('')
    } else expect(dialog.style.maxWidth).toBe('var(--modal-width)')
  })

  it('balances nested optional page scroll locks and leaves default scroll policy unchanged', () => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'auto'
    const view = (first: boolean, second: boolean) => <>
      <Modal title="Image" open={first} lockBodyScroll onClose={() => {}}>Image</Modal>
      <Modal title="Diagram" open={second} lockBodyScroll onClose={() => {}}>Diagram</Modal>
    </>
    const { rerender, unmount } = render(view(true, true))
    expect(document.body.style.overflow).toBe('hidden')
    rerender(view(false, true))
    expect(document.body.style.overflow).toBe('hidden')
    rerender(view(false, false))
    expect(document.body.style.overflow).toBe('auto')
    unmount()
    const normal = render(<Modal title="Default" onClose={() => {}}>Body</Modal>)
    expect(document.body.style.overflow).toBe('auto')
    normal.unmount()
    document.body.style.overflow = previous
  })
})
