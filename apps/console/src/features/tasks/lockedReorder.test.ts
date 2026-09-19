import { describe, expect, it } from 'vitest'
import type { TaskItem } from '../../shared/data/api'
import { orderTaskRows, preserveLockedTaskRows } from './taskGraphState'

const task = (id: string, provider?: string, order?: number): TaskItem => ({ id, title: id, status: 'open', provider, order })

describe('locked task row reordering', () => {
  it('keeps project-provider rows in their original slots', () => {
    const rows = [task('a'), task('locked', 'project'), task('b'), task('c')]
    const proposal = [rows[3], rows[1], rows[2], rows[0]]

    expect(preserveLockedTaskRows(rows, proposal).map(row => row.id)).toEqual(['c', 'locked', 'b', 'a'])
  })

  it('ignores unknown and duplicate proposals without losing movable rows', () => {
    const rows = [task('a'), task('locked', 'project'), task('b')]
    const proposal = [task('b'), task('unknown'), task('b')]

    expect(preserveLockedTaskRows(rows, proposal).map(row => row.id)).toEqual(['b', 'locked', 'a'])
  })

  it('restores saved manual order before applying a local reorder', () => {
    const rows = [task('c', undefined, 3), task('a', undefined, 1), task('b', undefined, 2)]

    expect(orderTaskRows(rows).map(row => row.id)).toEqual(['a', 'b', 'c'])
    expect(orderTaskRows(rows, ['c', 'a', 'b']).map(row => row.id)).toEqual(['c', 'a', 'b'])
  })
})
