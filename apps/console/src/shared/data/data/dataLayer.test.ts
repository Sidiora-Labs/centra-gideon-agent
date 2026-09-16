import { useEffect, useState } from 'react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useQuery, invalidateKeys, writeQuery } from './index'


describe('useQuery holds data across a same-key refresh', () => {
  beforeEach(() => {
    invalidateKeys('', true)
    sessionStorage.clear()
  })

  it('keeps the previous value visible through invalidateKeys + refresh', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ n: 1 })
      .mockResolvedValueOnce({ n: 2 })
    const { result } = renderHook(() => useQuery('k:hold', fetcher))
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))

    const seen: (unknown)[] = []
    act(() => { invalidateKeys('k:hold'); result.current.refresh() })
    seen.push(result.current.data)
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }))

    expect(seen[0]).not.toBeUndefined()
    expect(seen[0]).toEqual({ n: 1 })
  })

  it('keeps data across a bare refresh (no invalidate)', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ n: 1 })
      .mockResolvedValueOnce({ n: 2 })
    const { result } = renderHook(() => useQuery('k:bare', fetcher))
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))

    act(() => { result.current.refresh() })
    expect(result.current.data).toEqual({ n: 1 })
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }))
  })

  it('a revalidation reports revalidating + stale, NOT loading', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ n: 1 })
      .mockResolvedValueOnce({ n: 2 })
    const { result } = renderHook(() => useQuery('k:loading', fetcher, { staleAfterMs: 60_000 }))
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))
    expect(result.current.stale, 'a value that just landed is fresh').toBe(false)

    act(() => { invalidateKeys('k:loading') })
    expect(result.current.data, 'the paint is HELD — no skeleton flash').toEqual({ n: 1 })
    expect(result.current.loading, 'there IS something to show, so this is not loading').toBe(false)
    expect(result.current.stale, 'and the surface is told it is not current').toBe(true)
    expect(result.current.revalidating).toBe(true)
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }))
    expect(result.current.stale, 'fresh data clears the label').toBe(false)
    expect(result.current.revalidating).toBe(false)
  })

  it('CLEARS on a genuine key change — one resource must not paint under another key', async () => {
    const fetcher = vi.fn(async () => ({ n: 1 }))
    const { result, rerender } = renderHook(
      ({ k }: { k: string }) => useQuery(k, fetcher),
      { initialProps: { k: 'k:a' } },
    )
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))

    rerender({ k: 'k:b' })
    expect(result.current.data).toBeUndefined()
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))
  })

  it('paints a warm key instantly on a key change (no flash when the cache has it)', async () => {
    writeQuery('k:warm', { n: 9 })
    const fetcher = vi.fn(async () => ({ n: 9 }))
    const { result, rerender } = renderHook(
      ({ k }: { k: string }) => useQuery(k, fetcher),
      { initialProps: { k: 'k:cold' } },
    )
    await waitFor(() => expect(result.current.data).toEqual({ n: 9 }))
    rerender({ k: 'k:warm' })
    expect(result.current.data).toEqual({ n: 9 })
  })

  it('a failed refresh leaves the last good value on screen', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ n: 1 })
      .mockRejectedValueOnce(new Error('network'))
    const { result } = renderHook(() => useQuery('k:err', fetcher))
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))

    act(() => { invalidateKeys('k:err'); result.current.refresh() })
    await waitFor(() => expect(result.current.error).toBeTruthy())
    expect(result.current.data).toEqual({ n: 1 })
  })

  it('hands back the SAME refresh function across renders', async () => {
    const fetcher = vi.fn(async () => ({ n: 1 }))
    const { result, rerender } = renderHook(() => useQuery('k:stable', fetcher))
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))
    const first = result.current.refresh
    rerender()
    rerender()
    expect(result.current.refresh).toBe(first)
  })

  it('does not refetch in a loop when a consumer depends on refresh', async () => {
    const fetcher = vi.fn(async () => ({ n: 1 }))
    function Consumer() {
      const { data, refresh } = useQuery('k:noloop', fetcher)
      const [reloadKey, setReloadKey] = useState(0)
      useEffect(() => { if (reloadKey) refresh() }, [reloadKey, refresh])
      useEffect(() => { setReloadKey(1) }, [])
      return data ? 'ready' : 'pending'
    }
    renderHook(() => Consumer())
    await waitFor(() => expect(fetcher).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 60))
    expect(fetcher.mock.calls.length).toBeLessThanOrEqual(3)
  })

  it('revalidating covers a cached re-read, unlike loading', async () => {
    let release: (() => void) | null = null
    const fetcher = vi.fn(async () => {
      if (release) await new Promise<void>((r) => { release = r })
      return { n: 1 }
    })
    const { result } = renderHook(() => useQuery('k:reval', fetcher))
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }))
    expect(result.current.loading).toBe(false)
    expect(result.current.revalidating).toBe(false)

    let resolveHeld: (() => void) | undefined
    const held = new Promise<void>((r) => { resolveHeld = r })
    fetcher.mockImplementationOnce(async () => { await held; return { n: 2 } })
    act(() => { result.current.refresh() })
    await waitFor(() => expect(result.current.revalidating).toBe(true))
    expect(result.current.loading).toBe(false)
    expect(result.current.data).toEqual({ n: 1 })

    await act(async () => { resolveHeld?.(); await Promise.resolve() })
    await waitFor(() => expect(result.current.revalidating).toBe(false))
    expect(result.current.data).toEqual({ n: 2 })
  })
})
