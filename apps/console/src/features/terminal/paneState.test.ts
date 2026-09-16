import { describe, it, expect } from 'vitest'
import { panesAfterClose, type PaneSelection } from './paneState'

function close(tabs: readonly string[], closed: string, panes: PaneSelection) {
  return panesAfterClose(tabs.filter((id) => id !== closed), closed, panes)
}

describe('panesAfterClose', () => {
  it('clears the split when the last remaining tab IS the split (the #645 repro)', () => {
    expect(close(['f224c6b61802', '781be9c1729c'], 'f224c6b61802', {
      active: 'f224c6b61802', split: '781be9c1729c',
    })).toEqual({ active: '781be9c1729c', split: null })
  })

  it('keeps the split when a distinct session can still fill the right pane', () => {
    expect(close(['a', 'b', 'c'], 'c', { active: 'c', split: 'b' }))
      .toEqual({ active: 'a', split: 'b' })
  })

  it('never promotes into the split, even when the split is the last tab in order', () => {
    expect(close(['a', 'b', 'c'], 'a', { active: 'a', split: 'c' }))
      .toEqual({ active: 'b', split: 'c' })
  })

  it('closes the split pane itself without disturbing the active pane', () => {
    expect(close(['a', 'b'], 'b', { active: 'a', split: 'b' }))
      .toEqual({ active: 'a', split: null })
  })

  it('leaves both panes alone when a hidden tab closes', () => {
    expect(close(['a', 'b', 'c'], 'c', { active: 'a', split: 'b' }))
      .toEqual({ active: 'a', split: 'b' })
  })

  it('promotes the last remaining tab when no split is open', () => {
    expect(close(['a', 'b', 'c'], 'a', { active: 'a', split: null }))
      .toEqual({ active: 'c', split: null })
  })

  it('empties both panes when the only session closes', () => {
    expect(close(['a'], 'a', { active: 'a', split: null }))
      .toEqual({ active: '', split: null })
  })

  it('empties both panes when the split pane is the last one standing', () => {
    expect(panesAfterClose([], 'a', { active: 'a', split: 'b' }))
      .toEqual({ active: '', split: null })
  })

  it('is idempotent under a repeated close (a double-clicked chip)', () => {
    const once = close(['a', 'b'], 'a', { active: 'a', split: 'b' })
    expect(once).toEqual({ active: 'b', split: null })
    expect(close(['b'], 'a', once)).toEqual({ active: 'b', split: null })
  })
})
