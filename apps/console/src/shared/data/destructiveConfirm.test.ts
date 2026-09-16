import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

describe('destructive routes are called with an explicit confirm', () => {
  let calls: Array<{ url: string; body: unknown }>

  beforeEach(() => {
    calls = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : undefined })
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }),
    )
  })

  afterEach(() => vi.unstubAllGlobals())

  it('mergeKnowledgeTag sends confirm, because merging deletes the source tag', async () => {
    const { api } = await import('./api')
    await api.mergeKnowledgeTag(1, 2)
    const call = calls.find((c) => c.url.includes('/merge'))
    expect(call, 'no merge request was made').toBeTruthy()
    expect(call!.body).toMatchObject({ into: 2, confirm: true })
  })

  it('resetTaskList sends confirm, because reset clears execution notes', async () => {
    const { api } = await import('./api')
    await api.resetTaskList('list-1')
    const call = calls.find((c) => c.url.includes('/reset'))
    expect(call, 'no reset request was made').toBeTruthy()
    expect(call!.body).toMatchObject({ confirm: true })
  })
})
