
import { describe, expect, it } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useFileTabs } from './useFileTabs'

const entry = (path: string) => ({ name: path.split('/').pop() || path, path, is_dir: false })

let seq = 0
const freshScope = () => `rename-test-${++seq}`

describe('renamePath', () => {
  it('re-points the tab, its name, and the active path', () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => result.current.open(entry('notes/q3-final.md')))
    expect(result.current.tabs.map((t) => t.path)).toEqual(['notes/q3-final.md'])

    act(() => result.current.renamePath('notes/q3-final.md', 'notes/q3-v2.md'))

    expect(result.current.tabs.map((t) => t.path)).toEqual(['notes/q3-v2.md'])
    expect(result.current.tabs[0].name).toBe('q3-v2.md')
    expect(result.current.activePath).toBe('notes/q3-v2.md')
    expect(result.current.active?.path).toBe('notes/q3-v2.md')
  })

  it('carries the dirty flag to the new path rather than leaving it on the old', () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => result.current.open(entry('a.md')))
    act(() => result.current.markDirty('a.md', true))

    act(() => result.current.renamePath('a.md', 'b.md'))

    expect(result.current.dirty['b.md']).toBe(true)
    expect(result.current.dirty['a.md']).toBeUndefined()
  })

  it('moves every tab under a renamed DIRECTORY', () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => {
      result.current.open(entry('notes/a.md'))
      result.current.open(entry('notes/deep/b.md'))
      result.current.open(entry('other/c.md'))
    })

    act(() => result.current.renamePath('notes', 'journal'))

    expect(result.current.tabs.map((t) => t.path).sort()).toEqual([
      'journal/a.md', 'journal/deep/b.md', 'other/c.md',
    ])
  })

  it('does not claim a SIBLING whose name merely starts the same way', () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => {
      result.current.open(entry('notes/a.md'))
      result.current.open(entry('notes-archive/b.md'))
    })

    act(() => result.current.renamePath('notes', 'journal'))

    expect(result.current.tabs.map((t) => t.path).sort()).toEqual([
      'journal/a.md', 'notes-archive/b.md',
    ])
  })

  it('leaves everything alone when nothing matches', () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => result.current.open(entry('a.md')))
    const before = result.current.tabs

    act(() => result.current.renamePath('somewhere/else.md', 'other.md'))

    expect(result.current.tabs).toBe(before)
  })

  it('ignores a no-op or an empty path', () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => result.current.open(entry('a.md')))

    act(() => result.current.renamePath('a.md', 'a.md'))
    act(() => result.current.renamePath('', 'b.md'))
    act(() => result.current.renamePath('a.md', ''))

    expect(result.current.tabs.map((t) => t.path)).toEqual(['a.md'])
    expect(result.current.activePath).toBe('a.md')
  })
})

describe('tabsUnder', () => {
  it('reports the tabs a rename or delete of a path would affect', () => {
    const { result } = renderHook(() => useFileTabs(freshScope()))
    act(() => {
      result.current.open(entry('notes/a.md'))
      result.current.open(entry('notes/deep/b.md'))
      result.current.open(entry('notes-archive/c.md'))
      result.current.open(entry('other.md'))
    })

    expect(result.current.tabsUnder('notes').map((t) => t.path).sort()).toEqual([
      'notes/a.md', 'notes/deep/b.md',
    ])
    expect(result.current.tabsUnder('other.md').map((t) => t.path)).toEqual(['other.md'])
    expect(result.current.tabsUnder('nothing/here').map((t) => t.path)).toEqual([])
  })
})
