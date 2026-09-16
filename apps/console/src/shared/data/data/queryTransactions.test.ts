import { beforeEach, describe, expect, it } from 'vitest'
import { fetchKey, invalidateKeys, peekEntry, resetDataStore, writeQuery } from './store'

beforeEach(resetDataStore)

describe('cache request ownership', () => {
  it('does not let a pre-invalidation response replace the newer committed value', async () => {
    let finishOld!: (value: string) => void
    const old = fetchKey('tasks:race', () => new Promise<string>((resolve) => { finishOld = resolve }))
    invalidateKeys('tasks:race')
    await fetchKey('tasks:race', async () => 'current')
    finishOld('obsolete')
    expect(await old).toBe('obsolete')
    expect(peekEntry<string>('tasks:race')?.value).toBe('current')
  })

  it('keeps an authoritative direct write when an older read finishes later', async () => {
    let finish!: (value: number) => void
    const reading = fetchKey('tasks:direct', () => new Promise<number>((resolve) => { finish = resolve }))
    writeQuery('tasks:direct', 2)
    finish(1)
    await reading
    expect(peekEntry<number>('tasks:direct')?.value).toBe(2)
  })

  it('cannot resurrect a cleared store from a pending read', async () => {
    let finish!: (value: string) => void
    const reading = fetchKey('tasks:reset', () => new Promise<string>((resolve) => { finish = resolve }))
    resetDataStore()
    finish('old lifetime')
    await reading
    expect(peekEntry('tasks:reset')).toBeUndefined()
  })

  it('remembers persistence requested by a later observer joining the same read', async () => {
    let finish!: (value: number) => void
    const first = fetchKey('settings:shared', () => new Promise<number>((resolve) => { finish = resolve }))
    const second = fetchKey('settings:shared', async () => 99, true)
    expect(second).toBe(first)
    finish(7)
    await second
    expect(JSON.parse(sessionStorage.getItem('cache:settings:shared')!).v).toBe(7)
  })
})
