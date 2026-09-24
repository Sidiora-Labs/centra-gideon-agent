import { beforeEach, describe, expect, it } from 'vitest'
import { fetchKey, isFetching, peekEntry, READER_WAIT_MS, resetDataStore } from './store'

beforeEach(resetDataStore)

describe('bounded cache readers', () => {
  it('releases all waiting readers at eight seconds and ignores a late response', async () => {
    expect(READER_WAIT_MS).toBe(8_000)
    let finish!: (value: string) => void
    const source = new Promise<string>((resolve) => { finish = resolve })
    const start = performance.now()
    const request = fetchKey('first-run:reader', () => source)
    const joined = fetchKey('first-run:reader', () => Promise.resolve('unused'))
    expect(joined).toBe(request)
    expect(isFetching('first-run:reader')).toBe(true)
    await expect(request).rejects.toThrow('timed out after 8 seconds')
    const elapsed = performance.now() - start
    expect(elapsed).toBeGreaterThanOrEqual(READER_WAIT_MS - 50)
    expect(elapsed).toBeLessThan(READER_WAIT_MS + 2_000)
    expect(isFetching('first-run:reader')).toBe(false)
    expect(peekEntry('first-run:reader')).toBeUndefined()
    expect(await fetchKey('first-run:reader', () => Promise.resolve('recovered'))).toBe('recovered')
    finish('obsolete')
    await source
    expect(peekEntry<string>('first-run:reader')?.value).toBe('recovered')
  }, 15_000)

  it('preserves fast successes and immediate failures', async () => {
    expect(await fetchKey('reader:success', () => Promise.resolve(42))).toBe(42)
    expect(isFetching('reader:success')).toBe(false)
    await expect(fetchKey('reader:error', () => Promise.reject(new Error('offline')))).rejects.toThrow('offline')
    expect(isFetching('reader:error')).toBe(false)
  })
})
