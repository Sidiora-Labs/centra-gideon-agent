import { describe, expect, it } from 'vitest'

import { byInstancePath, compareInstancePaths } from './instancePathOrder'


const sortPaths = (paths: string[]) => [...paths].sort(compareInstancePaths)

describe('compareInstancePaths', () => {
  it('orders fan-out indices numerically past the tenth item', () => {
    const shuffled = ['n[11]', 'n[2]', 'n[20]', 'n[9]', 'n[1]', 'n[10]']
    expect(sortPaths(shuffled)).toEqual(['n[1]', 'n[2]', 'n[9]', 'n[10]', 'n[11]', 'n[20]'])
  })

  it('disagrees with localeCompare exactly where the engine does', () => {
    const shuffled = ['n[11]', 'n[2]', 'n[20]', 'n[9]', 'n[1]', 'n[10]']
    const locale = [...shuffled].sort((a, b) => a.localeCompare(b))
    expect(locale).toEqual(['n[1]', 'n[10]', 'n[11]', 'n[2]', 'n[20]', 'n[9]'])
    expect(sortPaths(shuffled)).not.toEqual(locale)
  })

  it('orders loop iterations numerically — the `@n` form', () => {
    const shuffled = ['root.body@10', 'root.body@2', 'root.body@11', 'root.body@1']
    expect(sortPaths(shuffled)).toEqual([
      'root.body@1', 'root.body@2', 'root.body@10', 'root.body@11',
    ])
  })

  it('orders foreach items numerically — the `#n` form', () => {
    const shuffled = ['root.body#10', 'root.body#2', 'root.body#0', 'root.body#11', 'root.body#9']
    expect(sortPaths(shuffled)).toEqual([
      'root.body#0', 'root.body#2', 'root.body#9', 'root.body#10', 'root.body#11',
    ])
  })

  it('keeps a container ahead of its own descendants', () => {
    const shuffled = [
      'n[2].child[1]', 'n[10]', 'n[1].child[0]', 'n[2]', 'n[1]', 'n[1].child[10]',
      'n[1].child[2]',
    ]
    expect(sortPaths(shuffled)).toEqual([
      'n[1]',
      'n[1].child[0]',
      'n[1].child[2]',
      'n[1].child[10]',
      'n[2]',
      'n[2].child[1]',
      'n[10]',
    ])
  })

  it('orders a nested loop inside a fan-out at every level', () => {
    const shuffled = [
      'root.children[10].body@2', 'root.children[2].body@10', 'root.children[2].body@2',
      'root.children[10].body@10',
    ]
    expect(sortPaths(shuffled)).toEqual([
      'root.children[2].body@2',
      'root.children[2].body@10',
      'root.children[10].body@2',
      'root.children[10].body@10',
    ])
  })

  it('compares digits numerically inside a node id too', () => {
    expect(sortPaths(['step10', 'step2', 'step1'])).toEqual(['step1', 'step2', 'step10'])
  })

  it('orders unindexed segments as text', () => {
    expect(sortPaths(['root.cases[standard]', 'root.cases[deep]'])).toEqual([
      'root.cases[deep]', 'root.cases[standard]',
    ])
  })

  it('treats equal values spelled differently as equal, like the engine', () => {
    expect(compareInstancePaths('n[02]', 'n[2]')).toBe(0)
  })

  it('is a total order: antisymmetric, reflexive, and transitive', () => {
    const paths = ['n[1]', 'n[2]', 'n[10]', 'n[1].child[0]', 'root.body@11', 'step2', '']
    for (const a of paths) {
      expect(compareInstancePaths(a, a)).toBe(0)
      for (const b of paths) {
        const forward = Math.sign(compareInstancePaths(a, b))
        expect(forward).toBe(-Math.sign(compareInstancePaths(b, a)) || 0)
      }
    }
    const asc = sortPaths(paths)
    const descThenSorted = sortPaths([...paths].reverse())
    expect(descThenSorted).toEqual(asc)
  })

  it('handles empty and index-only paths without throwing', () => {
    expect(compareInstancePaths('', '')).toBe(0)
    expect(sortPaths(['', 'n[0]'])).toEqual(['', 'n[0]'])
    expect(sortPaths(['20', '3', '100'])).toEqual(['3', '20', '100'])
  })
})

describe('byInstancePath', () => {
  it('sorts node-shaped records, not just bare strings', () => {
    const nodes = [
      { instance_path: 'root.body#10', state: 'done' },
      { instance_path: 'root.body#2', state: 'done' },
      { instance_path: 'root.body#1', state: 'done' },
    ]
    expect([...nodes].sort(byInstancePath).map((n) => n.instance_path)).toEqual([
      'root.body#1', 'root.body#2', 'root.body#10',
    ])
  })
})
