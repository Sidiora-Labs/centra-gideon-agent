import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { tabListKeys } from './tabListKeys'


function Strip({ onSelect = vi.fn(), n = 3, selected = 0 }: { onSelect?: (i: number) => void; n?: number; selected?: number }) {
  return (
    <div role="tablist" aria-label="Test" onKeyDown={tabListKeys(onSelect)}>
      {Array.from({ length: n }, (_, i) => (
        <button key={i} type="button" role="tab" aria-selected={i === selected} tabIndex={i === selected ? 0 : -1}>
          tab{i}
        </button>
      ))}
    </div>
  )
}

const tabs = () => screen.getAllByRole('tab')

describe('tabListKeys', () => {
  it('ArrowRight moves to the next tab and selects it', () => {
    const onSelect = vi.fn()
    render(<Strip onSelect={onSelect} />)
    tabs()[0].focus()
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' })
    expect(onSelect).toHaveBeenCalledWith(1)
    expect(document.activeElement).toBe(tabs()[1])
  })

  it('wraps in both directions, so neither end dead-ends', () => {
    const onSelect = vi.fn()
    render(<Strip onSelect={onSelect} selected={2} />)
    tabs()[2].focus()
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' })
    expect(onSelect).toHaveBeenLastCalledWith(0)
    tabs()[0].focus()
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowLeft' })
    expect(onSelect).toHaveBeenLastCalledWith(2)
  })

  it('Home and End jump to the ends', () => {
    const onSelect = vi.fn()
    render(<Strip onSelect={onSelect} selected={1} />)
    tabs()[1].focus()
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'End' })
    expect(onSelect).toHaveBeenLastCalledWith(2)
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'Home' })
    expect(onSelect).toHaveBeenLastCalledWith(0)
  })

  it('falls back to the SELECTED tab when focus is not on one', () => {
    const onSelect = vi.fn()
    render(<Strip onSelect={onSelect} selected={1} />)
    ;(document.activeElement as HTMLElement)?.blur()
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' })
    expect(onSelect).toHaveBeenCalledWith(2)
  })

  it('ignores keys that are not navigation', () => {
    const onSelect = vi.fn()
    render(<Strip onSelect={onSelect} />)
    for (const key of ['a', 'Enter', ' ', 'Tab', 'ArrowUp', 'Escape']) {
      fireEvent.keyDown(screen.getByRole('tablist'), { key })
    }
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('does nothing for a single tab, so a one-session strip is not a trap', () => {
    const onSelect = vi.fn()
    render(<Strip onSelect={onSelect} n={1} />)
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' })
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('skips a disabled tab', () => {
    const onSelect = vi.fn()
    render(
      <div role="tablist" aria-label="Test" onKeyDown={tabListKeys(onSelect)}>
        <button type="button" role="tab" aria-selected tabIndex={0}>a</button>
        <button type="button" role="tab" aria-selected={false} tabIndex={-1} disabled>b</button>
        <button type="button" role="tab" aria-selected={false} tabIndex={-1}>c</button>
      </div>,
    )
    screen.getByRole('tab', { name: 'a' }).focus()
    fireEvent.keyDown(screen.getByRole('tablist'), { key: 'ArrowRight' })
    expect(onSelect).toHaveBeenCalledWith(1)
  })
})

describe('every tab strip in the tree is a real tablist', () => {
  const SRC = join(process.cwd(), "src")
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  const sites = () => walk(SRC)
    .map((abs) => ({ file: abs.slice(SRC.length + 1), src: readFileSync(abs, 'utf8') }))
    .filter((f) => /role="tab"/.test(f.src))

  it('finds the population (not vacuously green)', () => {
    expect(sites().length, 'the role="tab" census must not go empty').toBeGreaterThanOrEqual(6)
  })

  it('each one declares a tablist, roving tabIndex and aria-selected', () => {
    const bad: string[] = []
    for (const { file, src } of sites()) {
      const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      const problems = [
        /role="tablist"/.test(code) ? '' : 'no role="tablist"',
        /aria-selected/.test(code) ? '' : 'no aria-selected',
        /tabIndex=\{[^}]*\?\s*0\s*:\s*-1\}/.test(code) ? '' : 'no roving tabIndex',
      ].filter(Boolean)
      if (problems.length) bad.push(`${file}: ${problems.join(', ')}`)
    }
    expect(bad, `a strip announces tabs without being a tablist:\n${bad.join('\n')}`).toEqual([])
  })

  it('the arrow-key handler has ONE implementation, shared by four strips', () => {
    const adopters = sites().filter((f) => /tabListKeys\(/.test(f.src)).map((f) => f.file)
    expect(adopters.length, `adopters: ${adopters.join(', ')}`).toBeGreaterThanOrEqual(4)
    for (const { file, src } of sites()) {
      if (file === 'shared/data/tabListKeys.ts') continue
      const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      expect(code, `${file} re-implements arrow navigation`).not.toMatch(/'ArrowLeft'.*'ArrowRight'|ArrowLeft' \?/)
    }
  })

  it('shared/ui/Segmented is the one deliberate exception, and it is named', () => {
    const seg = readFileSync(join(SRC, 'shared/ui/Segmented.tsx'), 'utf8')
    expect(seg).toMatch(/role="tablist"/)
    expect(seg, 'Segmented must NOT adopt tab arrow-nav before the owner rules').not.toMatch(/tabListKeys/)
  })
})
