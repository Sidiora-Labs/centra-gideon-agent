import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { eventIsAgentScoped } from './triggerMeta'
import type { TriggerVariables } from '../../shared/data/api'


const cat = (over: Partial<TriggerVariables['lifecycle'][number]>[] = []): TriggerVariables => ({
  schedule: [],
  app_sources: [],
  lifecycle: [
    { event: 'MemoryWrite', label: 'Memory write', desc: '', vars: [], blocking: false, agent_scoped: false },
    { event: 'Error', label: 'Error', desc: '', vars: [], blocking: false, agent_scoped: true },
    ...over,
  ] as TriggerVariables['lifecycle'],
})

describe('eventIsAgentScoped reads the server catalog', () => {
  it('true only where the catalog says so', () => {
    expect(eventIsAgentScoped(cat(), 'Error')).toBe(true)
    expect(eventIsAgentScoped(cat(), 'MemoryWrite')).toBe(false)
  })

  it('makes NO claim while loading, on an older backend, or for an unknown event', () => {
    expect(eventIsAgentScoped(null, 'Error')).toBe(false)
    expect(eventIsAgentScoped(cat(), 'NotAnEvent')).toBe(false)
    expect(eventIsAgentScoped(cat(), undefined)).toBe(false)
    const legacy = cat()
    delete (legacy.lifecycle[1] as { agent_scoped?: boolean }).agent_scoped
    expect(eventIsAgentScoped(legacy, 'Error')).toBe(false)
  })
})

describe('the badge and the disclosure carry the new vocabulary (source-level)', () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
  const list = strip(readFileSync(resolve(__dirname, "TriggersListPage.tsx"), 'utf8'))
  const create = strip(readFileSync(resolve(__dirname, "TriggerCreatePage.tsx"), 'utf8'))

  it("the list badge no longer computes 'dormant' from usedBy", () => {
    expect(list).not.toMatch(/usedBy\.length === 0 &&[^}]*· dormant/)
    expect(list).toMatch(/eventIsDormant\(catalog, t\.hook\?\.event\)/)
    expect(list).toMatch(/eventIsAgentScoped\(catalog, t\.hook\?\.event\)[^}]*no agent references this/)
  })

  it('the create form disclosures agent scoping at the point of choice', () => {
    expect(create).toMatch(/eventIsAgentScoped\(catalog, event\)/)
    expect(create).toContain('agent-scoped: it fires only for agents whose')
  })
})
