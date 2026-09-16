import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
  const p = join(d, n)
  if (statSync(p).isDirectory()) return walk(p)
  return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
})
const codeOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('every reader of the chat-session collection shares its namespace', () => {
  it('the dashboard reads it under the collection prefix, not a surface name', () => {
    const dash = codeOf('features/dashboard/DashboardPage.tsx')
    expect(dash, 'the key must sit in the collection namespace').toMatch(/'chat:sessions:recent'/)
    expect(dash, 'the surface-named key is gone').not.toMatch(/dashboard:recent-sessions/)
  })

  it('no reader of api.chatSessions() sits outside the prefix', () => {
    const strays: string[] = []
    for (const abs of walk(SRC)) {
      const code = codeOf(abs.slice(SRC.length + 1))
      if (!code.includes('api.chatSessions(')) continue
      for (const m of code.matchAll(/useQuery(?:<[^>]*>)?\(\s*([^,]+),/g)) {
        const keyExpr = m[1]
        if (!/chatSessions/.test(code.slice(m.index!, m.index! + 240))) continue
        if (!/'chat:sessions/.test(keyExpr)) strays.push(`${abs.slice(SRC.length + 1)}: ${keyExpr.trim().slice(0, 60)}`)
      }
    }
    expect(strays, `these read the collection under an unreachable key:\n${strays.join('\n')}`).toEqual([])
  })

  it('the prefix bust reaches all three keys and nothing else', async () => {
    const { invalidateKeys, writeQuery, peekQuery } = await import('../../shared/data/data')
    writeQuery('chat:sessions', ['sidebar'])
    writeQuery('chat:sessions:archived', ['history'])
    writeQuery('chat:sessions:recent', ['dashboard'])
    writeQuery('chat:suggestions', ['keep me'])
    writeQuery('chat:folders', ['keep me too'])

    invalidateKeys('chat:sessions', true)

    expect(peekQuery('chat:sessions')).toBeUndefined()
    expect(peekQuery('chat:sessions:archived')).toBeUndefined()
    expect(peekQuery('chat:sessions:recent'), "the dashboard's list is dropped").toBeUndefined()
    expect(peekQuery('chat:suggestions'), 'a namespace sibling must survive').toEqual(['keep me'])
    expect(peekQuery('chat:folders')).toEqual(['keep me too'])
  })

  it('persist:true means the stale row survived a reload — so the bust clears storage too', async () => {
    const { invalidateKeys, writeQuery } = await import('../../shared/data/data')
    writeQuery('chat:sessions:recent', ['dashboard'])
    invalidateKeys('chat:sessions', true)
    const leftovers = Object.keys(sessionStorage).filter((k) => k.includes('chat:sessions'))
    expect(leftovers, `sessionStorage still holds ${leftovers.join(', ')}`).toEqual([])
  })
})

describe('both session mutations bust the collection', () => {
  const chat = () => codeOf('features/ChatPage.tsx')

  it('delete busts it', () => {
    const code = chat()
    const at = code.indexOf('const load = useCallback(')
    expect(at, 'the loader must still exist').toBeGreaterThan(-1)
    expect(code.slice(at, at + 400)).toMatch(/invalidateKeys\('chat:sessions', true\)/)
  })

  it('rename busts it — it used to bust nothing at all', () => {
    const code = chat()
    const at = code.indexOf('async function commitRename()')
    expect(at, 'commitRename must still exist').toBeGreaterThan(-1)
    const fn = code.slice(at, at + 700)
    expect(fn, 'the optimistic header update is not enough for three other readers')
      .toMatch(/invalidateKeys\('chat:sessions', true\)/)
  })

  it('the two per-key busts collapsed into one prefix call', () => {
    const code = chat()
    expect(code, 'the archived key no longer needs naming').not.toMatch(/invalidateKeys\('chat:sessions:archived'\)/)
    expect((code.match(/invalidateKeys\('chat:sessions', true\)/g) ?? []).length,
      'delete and rename both bust it').toBeGreaterThanOrEqual(2)
  })
})
