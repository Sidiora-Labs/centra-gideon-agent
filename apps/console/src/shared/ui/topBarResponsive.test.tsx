import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { TopBar } from './TopBar'

const css = readFileSync(join(process.cwd(), 'src/app/shell/shell.css'), 'utf8')
const mobile = css.split('@media (max-width: 768px) {')[1]?.split('@media (max-width: 767px) {')[0] ?? ''

function renderArtifactHeader(contentAligned = false) {
  const openLibrary = vi.fn()
  const iterate = vi.fn()
  const setCollection = vi.fn()
  const view = render(<div className="gideon-shell">
    <TopBar keepCornerPadding contentAligned={contentAligned}
      left={<div className="flex min-w-0 items-center gap-m">
        <button type="button" onClick={openLibrary}>Library</button>
        <span data-type="title-l">Quarterly operating results</span>
      </div>}
      right={<>
        <button type="button" onClick={iterate}>Iterate with agent</button>
        <button type="button" onClick={setCollection}>Set collection</button>
      </>} />
  </div>)
  const header = view.container.querySelector('header')!
  return { header, openLibrary, iterate, setCollection }
}

describe('shared TopBar responsive slots', () => {
  it.each([false, true])('keeps all artifact actions in DOM and keyboard order (contentAligned=%s)', async contentAligned => {
    const user = userEvent.setup()
    const { header, openLibrary, iterate, setCollection } = renderArtifactHeader(contentAligned)
    const left = header.querySelector('[data-header-left]')!
    const right = header.querySelector('[data-header-right]')!
    expect(left).toContainElement(screen.getByRole('button', { name: 'Library' }))
    expect(right).toContainElement(screen.getByRole('button', { name: 'Iterate with agent' }))
    expect(right).toContainElement(screen.getByRole('button', { name: 'Set collection' }))
    expect(right).not.toHaveAttribute('inert')
    expect(right).not.toHaveAttribute('aria-hidden')
    screen.getByRole('button', { name: 'Library' }).focus()
    await user.tab()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Iterate with agent' }))
    await user.keyboard('{Enter}')
    expect(iterate).toHaveBeenCalledOnce()
    await user.tab()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Set collection' }))
    await user.keyboard('{Enter}')
    expect(setCollection).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('button', { name: 'Library' }))
    expect(openLibrary).toHaveBeenCalledOnce()
    expect(header).toHaveClass('h-14')
  })

  it('reserves the shell-control band before either page slot at mobile width', () => {
    expect(mobile).toContain(".gideon-shell:not([data-hosted-mobile='true']) .gideon-topbar")
    expect(mobile).toContain('padding-top: max(64px, var(--shell-corner-rh, 0px))')
    expect(mobile).toContain('height: auto !important')
    expect(mobile).toContain('flex-wrap: wrap')
    expect(mobile).toContain('padding-inline: var(--spacing-m) !important')
  })

  it('gives title and action slots full separate rows with no clipped action container', () => {
    expect(mobile).toContain('> [data-header-left]')
    expect(mobile).toContain('> [data-header-right]')
    expect(mobile).toContain('flex: 0 0 100%')
    expect(mobile).toContain('min-height: 44px')
    expect(mobile).toContain('justify-content: flex-end')
    expect(mobile).not.toMatch(/overflow(?:-x|-y)?:\s*hidden/)
    expect(mobile).not.toMatch(/visibility:\s*hidden/)
  })

  it('does not reserve an empty shell-control band in hosted mobile mode', () => {
    expect(mobile).not.toMatch(/\[data-hosted-mobile='true'\].*padding-top:/)
    expect(mobile).toContain(".gideon-shell:not([data-hosted-mobile='true'])")
    const { header } = renderArtifactHeader()
    expect(header.querySelector('[data-header-left]')).toBeTruthy()
    expect(header.querySelector('[data-header-right]')).toBeTruthy()
  })
})
