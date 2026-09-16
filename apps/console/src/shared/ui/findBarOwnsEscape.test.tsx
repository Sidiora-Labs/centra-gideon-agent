import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, within, fireEvent, cleanup } from '@testing-library/react'
import { createRef } from 'react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { FindBar } from './FindBar'


const oneSegment = (s: string) => [s]

afterEach(() => cleanup())

function mount() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
  scrollRef.current = host

  const onClose = vi.fn()
  const outerEscape = vi.fn()
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') outerEscape() }
  window.addEventListener('keydown', onKey)

  const { container } = render(
    <FindBar items={['a paragraph of prose']} segmentsOf={oneSegment} nodeOf={() => null}
      scrollRef={scrollRef} label="Find in page" onClose={onClose} />,
  )
  return { container, onClose, outerEscape, detach: () => window.removeEventListener('keydown', onKey) }
}

describe('the focused find bar owns Escape', () => {
  it('closes itself and does NOT reach the surface behind it', () => {
    const { container, onClose, outerEscape, detach } = mount()
    try {
      fireEvent.keyDown(container.querySelector('[role="search"]')!, { key: 'Escape' })
      expect(onClose, 'the bar must still close').toHaveBeenCalledTimes(1)
      expect(outerEscape, 'a second layer also dismissed on one press').not.toHaveBeenCalled()
    } finally { detach() }
  })

  it('from the INPUT too — the same press, one tab stop deeper', () => {
    const { container, onClose, outerEscape, detach } = mount()
    try {
      fireEvent.keyDown(within(container).getByLabelText('Find in page'), { key: 'Escape' })
      expect(onClose).toHaveBeenCalledTimes(1)
      expect(outerEscape).not.toHaveBeenCalled()
    } finally { detach() }
  })

  it('🪤 does NOT swallow any other key — only Escape is claimed', () => {
    const { container, outerEscape, detach } = mount()
    const otherKeys: string[] = []
    const spy = (e: KeyboardEvent) => otherKeys.push(e.key)
    window.addEventListener('keydown', spy)
    try {
      const bar = container.querySelector('[role="search"]')!
      fireEvent.keyDown(bar, { key: 's', metaKey: true })
      fireEvent.keyDown(bar, { key: 'k', metaKey: true })
      fireEvent.keyDown(bar, { key: 'Enter' })
      expect(otherKeys, 'non-Escape keys must still reach the surface').toEqual(['s', 'k', 'Enter'])
      expect(outerEscape).not.toHaveBeenCalled()
    } finally { detach(); window.removeEventListener('keydown', spy) }
  })
})

describe('the fix is the propagation call, not the default call', () => {
  it('the handler stops propagation as well as the default', () => {
    const code = readFileSync(join(process.cwd(), "src/shared/ui/FindBar.tsx"), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const handler = code.match(/onKeyDown=\{\(e\) => \{ if \(e\.key === 'Escape'\)([^}]*)\}/)?.[1] ?? ''
    expect(handler, 'found the container Escape handler').not.toBe('')
    expect(handler).toMatch(/e\.stopPropagation\(\)/)
    expect(handler).toMatch(/e\.preventDefault\(\)/)
  })
})
