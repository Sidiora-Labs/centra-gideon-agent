import { beforeEach, describe, expect, it, vi } from 'vitest'

describe('server-backed comment store', () => {
  beforeEach(() => { vi.resetModules() })

  it('uses server IDs, converts seconds to milliseconds, and sends the bulk envelope', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ comments: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ comment: { id: 'server-id', docId: 'd', docLabel: 'D', quote: 'q', comment: 'c', ts: 12 } }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ deleted: 1 }), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    const { commentStore } = await import('./commentStore')
    await commentStore.resync()
    await commentStore.add({ docId: 'd', docLabel: 'D', quote: 'q', comment: 'c' })
    expect(commentStore.all()[0]).toMatchObject({ id: 'server-id', ts: 12000 })
    await commentStore.removeMany(['server-id'])
    expect(JSON.parse(fetch.mock.calls.at(-1)?.[1]?.body as string)).toEqual({ ids: ['server-id'] })
    expect(localStorage.length).toBe(0)
  })
})
