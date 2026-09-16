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

describe('the task collection is busted as a collection, not as one key', () => {
  const list = () => codeOf('features/tasks/TasksListPage.tsx')

  it('every bust in the list page is prefix mode', () => {
    const code = list()
    const busts = code.match(/invalidateKeys\('tasks'[^)]*\)/g) ?? []
    expect(busts.length, 'the loader plus both LoadError retries').toBe(3)
    for (const b of busts) expect(b, 'exact-key mode cannot reach `tasks-all`').toMatch(/'tasks', true\)/)
  })

  it('creating a task busts the collection — it used to bust nothing at all', () => {
    const code = codeOf('features/tasks/TaskCreatePage.tsx')
    const at = code.indexOf('await api.createTask(')
    expect(at, 'the create must still be here').toBeGreaterThan(-1)
    expect(code.slice(at, at + 300), "the task you just made is the one you want to depend on")
      .toMatch(/invalidateKeys\('tasks', true\)/)
  })

  it('the prefix reaches both keys and clears the persisted copy', async () => {
    const { invalidateKeys, writeQuery, peekQuery } = await import('./data')
    writeQuery('tasks', ['list'])
    writeQuery('tasks-all', ['dependency picker'])
    writeQuery('triggers', ['untouched'])

    invalidateKeys('tasks', true)

    expect(peekQuery('tasks')).toBeUndefined()
    expect(peekQuery('tasks-all'), "the dependency picker's copy is dropped").toBeUndefined()
    expect(peekQuery('triggers'), 'an unrelated collection must survive').toEqual(['untouched'])
  })

  it('persist:true means the wrong list is repainted after a reload — so storage is cleared too', async () => {
    const { invalidateKeys, writeQuery } = await import('./data')
    writeQuery('tasks-all', ['stale'])
    invalidateKeys('tasks', true)
    const leftovers = Object.keys(sessionStorage).filter((k) => k.includes('tasks'))
    expect(leftovers, `sessionStorage still holds ${leftovers.join(', ')}`).toEqual([])
  })
})

describe('both readers of the loaded-model set share one key', () => {
  it('neither key is named after its surface any more', () => {
    const panel = codeOf('features/settings/ModelsPanel.tsx')
    const widget = codeOf('features/dashboard/widgets/OnThisMachine.tsx')
    expect(panel).toMatch(/useQuery\('models:loaded'/)
    expect(widget).toMatch(/useQuery\('models:loaded'/)
    expect(panel + widget, 'the surface-named keys are gone')
      .not.toMatch(/settings:models-loaded|dashboard:on-this-machine/)
  })

  it('both Unload paths bust it, not just their own hook', () => {
    for (const rel of ['features/settings/ModelsPanel.tsx', 'features/dashboard/widgets/OnThisMachine.tsx']) {
      const code = codeOf(rel)
      const at = code.indexOf('await api.unloadModelProvider(')
      expect(at, `${rel} must still unload`).toBeGreaterThan(-1)
      expect(code.slice(at, at + 420), `${rel}: refresh() only refetches this surface`)
        .toMatch(/invalidateKeys\('models:loaded'\)/)
    }
  })
})

describe('the general check, so the fifth instance is caught by a test', () => {
  it('no collection is read under keys in two different namespaces', () => {
    const byCall = new Map<string, Set<string>>()
    let reads = 0
    for (const abs of walk(SRC)) {
      const code = codeOf(abs.slice(SRC.length + 1))
      for (const m of code.matchAll(/useQuery(?:<[^>]*>)?\(\s*'([^']+)'\s*,\s*([\s\S]{0,120}?)\)\s*(?:,|\))/g)) {
        const [, key, body] = m
        const call = body.match(/api\.(\w+)\(/)?.[1]
        if (!call) continue
        reads++
        const ns = key.includes(':') ? key.slice(0, key.indexOf(':')) : key.replace(/-.*$/, '')
        if (!byCall.has(call)) byCall.set(call, new Set())
        byCall.get(call)!.add(ns)
      }
    }
    expect(reads, 'the sweep must actually have found the collection reads').toBeGreaterThan(40)

    const KNOWN_DISTINCTIONS = new Set([
      'uLoops',
      'system',
      'modelProviderTypes',
    ])

    const PENDING_JUDGMENT = new Set([
      'appCatalog', 'dashboardConfig', 'gideonConfig',
    ])
    const split = [...byCall].filter(([c, ns]) => ns.size > 1 && !KNOWN_DISTINCTIONS.has(c))
      .map(([c]) => c)

    const unexpected = split.filter((c) => !PENDING_JUDGMENT.has(c))
    expect(unexpected, `these newly read one collection under two namespaces:\n${unexpected.join('\n')}`)
      .toEqual([])
    expect(split, 'the task collection is one namespace again').not.toContain('tasks')
    expect(split, 'the loaded-model set is one key again').not.toContain('modelsLoaded')
    expect(split.length, `PENDING_JUDGMENT is stale — prune the entries that are now single-namespace`)
      .toBe(PENDING_JUDGMENT.size)
  })

  it('the loops split stays split — it encodes disjoint subsets', () => {
    const code = codeOf('features/loops/LoopsListPage.tsx')
    expect(code, 'the non-code filter is what makes the two keys disjoint')
      .toMatch(/useQuery<GoalLoop\[\]>\('loops'[\s\S]{0,160}?kind !== 'code'/)
  })
})

describe('the sharpest instance in the un-separable pair, fixed ahead of it', () => {
  it("saving a chat setting busts the key that decides how streamed text reveals", () => {
    const code = codeOf('features/settings/ChatPanel.tsx')
    const writers = [...code.matchAll(/api\.saveDashboardConfig\(patch\)/g)]
    expect(writers.length, 'both dashboard-config writers in this panel').toBe(2)
    for (const w of writers) {
      expect(code.slice(w.index!, w.index! + 420), 'each writer must reach the consumer')
        .toMatch(/invalidateKeys\('chat:stream-reveal'\)/)
    }
  })

  it('`settings:chat` is a COMPOSITE, which is why the pair cannot be converged one at a time', () => {
    const code = codeOf('features/settings/ChatPanel.tsx')
    const at = code.indexOf("useQuery('settings:chat'")
    expect(at, 'the composite read must still be here').toBeGreaterThan(-1)
    const seg = code.slice(at, at + 600)
    expect(seg).toMatch(/api\.dashboardConfig\(\)/)
    expect(seg).toMatch(/api\.gideonConfig\(\)/)
  })
})
