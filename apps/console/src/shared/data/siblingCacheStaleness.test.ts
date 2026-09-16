import { describe, it, expect, vi, beforeEach } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const proposals = [{ id: 'p1', name: 'summarize-pr', description: 'x', procedure: 'y' }]

function mockApi(over: Record<string, unknown> = {}) {
  const named: Record<string, unknown> = {
    skillProposals: () => Promise.resolve({ proposals, lastReview: null }),
    acceptSkillProposal: () => Promise.resolve({ name: 'summarize-pr' }),
    rejectSkillProposal: () => Promise.resolve({ ok: true }),
    skills: () => Promise.resolve([]),
    ...over,
  }
  const api = new Proxy(named, {
    get(t, prop: string) { return prop in t ? t[prop] : () => Promise.resolve([]) },
  })
  vi.doMock('./api', async (orig) => ({ ...(await orig<Record<string, unknown>>()), api }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('a decision on one surface does not leave a sibling count stale', () => {
  it('busts every key on the proposals collection, not just its own', async () => {
    mockApi()
    const { invalidateKeys, writeQuery, peekQuery } = await import('./data')
    writeQuery('skill-proposals', proposals)
    writeQuery('skill-proposals-count', proposals)
    expect(peekQuery('skill-proposals-count'), 'the badge cache starts warm').toBeTruthy()

    invalidateKeys('skill-proposals', true)

    expect(peekQuery('skill-proposals'), 'the list cache is dropped').toBeUndefined()
    expect(peekQuery('skill-proposals-count'), "the badge's cache is dropped too").toBeUndefined()
  })

  it('prefix mode leaves unrelated collections alone', async () => {
    mockApi()
    const { invalidateKeys, writeQuery, peekQuery } = await import('./data')
    writeQuery('skill-proposals-count', proposals)
    writeQuery('skills', ['a'])
    writeQuery('artifacts:chat-picker', ['b'])
    invalidateKeys('skill-proposals', true)
    expect(peekQuery('skill-proposals-count')).toBeUndefined()
    expect(peekQuery('skills'), 'a neighbouring key must survive').toEqual(['a'])
    expect(peekQuery('artifacts:chat-picker'), 'another collection must survive').toEqual(['b'])
  })

  it('the artifacts namespace covers the chat picker', async () => {
    mockApi()
    const { invalidateKeys, writeQuery, peekQuery } = await import('./data')
    writeQuery('artifacts:chat-picker', ['b'])
    writeQuery('chat:suggestions', ['keep me'])
    invalidateKeys('artifacts:', true)
    expect(peekQuery('artifacts:chat-picker'), 'the picker cache is dropped').toBeUndefined()
    expect(peekQuery('chat:suggestions'), 'the chat namespace is untouched').toEqual(['keep me'])
  })
})

describe('every mutation site busts the collection it changed', () => {
  const SRC = join(process.cwd(), "src")
  const codeOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('all three proposal-decision sites bust the proposals prefix', () => {
    for (const rel of [
      'features/skills/SkillProposals.tsx',
      'features/inbox/InboxDetail.tsx',
      'features/dashboard/widgets/ActionCenter.tsx',
    ]) {
      expect(codeOf(rel), `${rel} must bust the whole collection`)
        .toMatch(/invalidateKeys\('skill-proposals', true\)/)
    }
  })

  it('both artifact mutation sites bust the artifacts namespace', () => {
    for (const rel of ['features/artifacts/ArtifactViewer.tsx', 'shared/ui/widget/WidgetFrame.tsx']) {
      expect(codeOf(rel), `${rel} must bust the artifacts namespace`)
        .toMatch(/invalidateKeys\('artifacts:', true\)/)
    }
  })

  it('the picker key lives in its COLLECTION namespace, not the chat one', () => {
    const chat = codeOf('features/ChatPage.tsx')
    expect(chat).toMatch(/useQuery\('artifacts:chat-picker'/)
    expect(chat, 'the old surface-namespaced key must be gone').not.toMatch(/chat:artifact-picker/)
  })

  it('prefix mode is actually reachable — it was dead code before this change', () => {
    const cache = codeOf('shared/data/data/store.ts')
    expect(cache, 'the primitive must still take the flag').toMatch(/invalidateKeys\(keyOrPrefix: string, prefix = false\)/)
    const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
    })
    const users = walk(SRC).filter((f) => /invalidateKeys\([^)]*,\s*true\)/.test(codeOf(f.slice(SRC.length + 1))))
    expect(users.length, 'prefix-mode call sites').toBeGreaterThanOrEqual(5)
  })
})
