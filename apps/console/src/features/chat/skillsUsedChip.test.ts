import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { insertActivity } from './coalesceReducers'
import {
  hydrateTurns,
  learnedSurface,
  skillsUsedLabel,
  skillsUsedTitle,
  stampActivityOrigin,
  type ActivitySegment,
  type HistMsg,
  type Segment,
  type SkillUsed,
} from './chatTypes'


const admitted = (name: string, tokens = 900): SkillUsed => ({ name, state: 'admitted', loaded_tokens: tokens })
const reduced = (name: string, tokens = 120): SkillUsed => ({ name, state: 'reduced', loaded_tokens: tokens })

describe('skillsUsedLabel — the count', () => {
  it('counts every entry the allocator loaded', () => {
    expect(skillsUsedLabel([admitted('a'), admitted('b'), admitted('c')])).toBe('used 3 skills')
  })

  it('counts a `reduced` skill: it loaded a SUMMARY, not nothing', () => {
    expect(skillsUsedLabel([admitted('a'), admitted('b'), reduced('c')])).toBe('used 3 skills')
  })

  it('says "skill", singular, for one', () => {
    expect(skillsUsedLabel([admitted('only')])).toBe('used 1 skill')
  })

  it('renders NOTHING for an empty list rather than a measured-looking zero', () => {
    expect(skillsUsedLabel([])).toBe('')
  })
})

describe('skillsUsedTitle — the names on hover', () => {
  it('lists the names in the ALLOCATOR’S order, not sorted', () => {
    const t = skillsUsedTitle([admitted('zebra'), admitted('alpha'), admitted('middle')])
    expect(t).toBe('Skills used this turn:\nzebra\nalpha\nmiddle')
    expect(t.indexOf('zebra')).toBeLessThan(t.indexOf('alpha'))
  })

  it('marks a `reduced` skill so a summary-only load never reads as a full one', () => {
    const t = skillsUsedTitle([admitted('full-body'), reduced('shrunk')])
    expect(t).toContain('shrunk — summary only')
    expect(t).not.toContain('full-body — summary only')
  })

  it('names an unnamed skill honestly instead of rendering a blank line', () => {
    expect(skillsUsedTitle([{ name: '', state: 'admitted', loaded_tokens: 0 }]))
      .toContain('(unnamed skill)')
  })

  it('is empty for an empty list', () => {
    expect(skillsUsedTitle([])).toBe('')
  })

  it('treats an unknown future state as a full load rather than inventing a marker', () => {
    expect(skillsUsedTitle([{ name: 'novel', state: 'promoted', loaded_tokens: 10 }]))
      .toBe('Skills used this turn:\nnovel')
  })
})

describe('learnedSurface — a tap lands where the artifact can be approved or edited', () => {
  it('routes a skill-ladder PROPOSAL to the Skills page’s proposals view', () => {
    const s = learnedSurface('proposal')
    expect(s?.href).toBe('#/skills?mode=proposals')
    expect(s?.href).not.toBe('#/skills')
  })

  it('routes an after-turn LESSON to the Memory Studio, which reads the lesson store', () => {
    expect(learnedSurface('lesson')?.href).toBe('#/settings/memory?tab=studio')
    expect(learnedSurface('lesson')?.href).not.toContain('/learning')
  })

  it('routes a preference FACET to the Memory Studio', () => {
    expect(learnedSurface('facet')?.href).toBe('#/settings/memory?tab=studio')
  })

  it('discriminates: a proposal and a lesson do NOT land on the same surface', () => {
    expect(learnedSurface('proposal')?.href).not.toBe(learnedSurface('lesson')?.href)
  })

  it('every mapped origin carries its own words, so the link never mislabels its target', () => {
    expect(learnedSurface('proposal')?.label).toContain('Skill proposals')
    expect(learnedSurface('lesson')?.label).toContain('lessons')
    expect(learnedSurface('proposal')?.label).not.toBe(learnedSurface('lesson')?.label)
  })

  describe('the degrade', () => {
    it('returns null for an ABSENT origin (every message persisted before T2.2)', () => {
      expect(learnedSurface(undefined)).toBeNull()
      expect(learnedSurface('')).toBeNull()
      expect(learnedSurface(null)).toBeNull()
    })

    it('returns null for an UNRECOGNISED origin rather than guessing a surface', () => {
      expect(learnedSurface('sop')).toBeNull()
      expect(learnedSurface('PROPOSAL')).toBeNull()
    })

    it('does not throw on any of them — the chip must still render', () => {
      for (const o of [undefined, null, '', 'sop', 'facet']) {
        expect(() => learnedSurface(o as string | null | undefined)).not.toThrow()
      }
    })
  })
})

describe('stampActivityOrigin — origin survives the LIVE stream, not just a reload', () => {
  const learned = (t: string) => ['learned', t] as const

  it('stamps the segment insertActivity spliced in', () => {
    const prev: Segment[] = []
    const [k, t] = learned('Learned: prefers tabs')
    const next = stampActivityOrigin(prev, insertActivity(prev, t, k, false), 'facet')
    const seg = next.find((s) => s.kind === 'activity') as ActivitySegment
    expect(seg.origin).toBe('facet')
    expect(seg.activityKind).toBe('learned')
  })

  it('stamps the NEW line only, leaving an earlier learned line’s origin alone', () => {
    const first: Segment[] = stampActivityOrigin([], insertActivity([], 'Learned: A', 'learned', false), 'facet')
    const second = stampActivityOrigin(first, insertActivity(first, 'Learned: B', 'learned', false), 'proposal')
    const segs = second.filter((s) => s.kind === 'activity') as ActivitySegment[]
    expect(segs).toHaveLength(2)
    expect(segs[0].origin).toBe('facet')
    expect(segs[1].origin).toBe('proposal')
  })

  it('is a no-op when insertActivity declined to insert (tool cards win)', () => {
    const withTool: Segment[] = [{ kind: 'tool', id: 't1', tool: 'Read', done: false } as Segment]
    const next = stampActivityOrigin(withTool, insertActivity(withTool, 'Learned: X', 'learned', false), 'lesson')
    expect(next).toBe(withTool)
    expect(next.some((s) => s.kind === 'activity')).toBe(false)
  })

  it('is a no-op for an ABSENT origin, so a pre-T2.2 frame stamps nothing', () => {
    const prev: Segment[] = []
    const next = stampActivityOrigin(prev, insertActivity(prev, 'Learned: Y', 'learned', false), '')
    const seg = next.find((s) => s.kind === 'activity') as ActivitySegment
    expect(seg).toBeDefined()
    expect(seg.origin).toBeUndefined()
    expect(learnedSurface(seg.origin)).toBeNull()
  })

  it('an unstamped line degrades, a stamped one routes — the two halves meet here', () => {
    const mk = (origin?: string) => {
      const next = stampActivityOrigin([], insertActivity([], 'Learned: Z', 'learned', false), origin)
      return (next.find((s) => s.kind === 'activity') as ActivitySegment).origin
    }
    expect(learnedSurface(mk('proposal'))?.href).toBe('#/skills?mode=proposals')
    expect(learnedSurface(mk(undefined))).toBeNull()
  })
})

describe('hydrateTurns — skills_used reaches the turn on reload', () => {
  const msg = (role: string, content: string, meta?: HistMsg['meta']): HistMsg =>
    ({ role, content, ts: `t-${content}`, ...(meta ? { meta } : {}) })

  it('carries meta.skills_used onto the assistant turn', () => {
    const turns = hydrateTurns([
      msg('user', 'hi'),
      msg('assistant', 'hello', { skills_used: [admitted('api-design'), reduced('runbook')] }),
    ])
    const a = turns.find((t) => t.role === 'assistant')
    expect(a?.skillsUsed).toEqual([admitted('api-design'), reduced('runbook')])
    expect(skillsUsedLabel(a!.skillsUsed!)).toBe('used 2 skills')
  })

  it('leaves it ABSENT on a turn with no meta — the pre-T2.1 case', () => {
    const turns = hydrateTurns([msg('user', 'hi'), msg('assistant', 'hello')])
    expect(turns.find((t) => t.role === 'assistant')?.skillsUsed).toBeUndefined()
  })

  it('leaves it absent for an empty array too, so no chip renders', () => {
    const turns = hydrateTurns([
      msg('user', 'hi'),
      msg('assistant', 'hello', { skills_used: [] }),
    ])
    expect(turns.find((t) => t.role === 'assistant')?.skillsUsed).toBeUndefined()
  })

  it('does not disturb the citations graft it sits beside', () => {
    const turns = hydrateTurns([
      msg('user', 'hi'),
      msg('assistant', 'hello', { memory_citations: [{ n: 1, id: 'e1' }] }),
    ])
    const a = turns.find((t) => t.role === 'assistant')
    expect(a?.citations).toHaveLength(1)
    expect(a?.skillsUsed).toBeUndefined()
  })
})

describe('the chip is wired at both surfaces (not an inert helper)', () => {
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')
  const chatPage = read('../ChatPage.tsx')
  const cockpit = read('../loops/LoopCockpitPage.tsx')
  const ledger = read('./ContextLedger.tsx')

  it('the chat run panel renders the label + the names on hover', () => {
    expect(chatPage).toContain('title={skillsUsedTitle(skills)}')
    expect(chatPage).toContain('{skillsUsedLabel(skills)}')
    expect(chatPage).toContain('skillsUsed.length > 0 && <SkillsUsedChip')
  })

  it('the loop cockpit renders the same two helpers in its status bar', () => {
    expect(cockpit).toContain('text={skillsUsedLabel(skillsUsed)}')
    expect(cockpit).toContain('title={skillsUsedTitle(skillsUsed)}')
    expect(cockpit).toContain('skillsUsed.length > 0 && <MetaPill')
  })

  it('the cockpit reads the meta over the EXISTING session endpoint, adding no channel', () => {
    expect(cockpit).toContain('api.chatSessionDetail(workerKey)')
    expect(cockpit).toContain('m.meta?.skills_used')
  })

  it('the learned row routes on origin instead of one hardcoded link', () => {
    expect(chatPage).toContain('stampActivityOrigin(segs, insertActivity(')
    expect(chatPage).toContain("ledger.learnedOrigin = (s as ActivitySegment).origin")
    expect(ledger).toContain('learnedSurface(learnedOrigin)')
    expect(ledger).toContain('<TextLink href={surface.href}>')
    expect(ledger).not.toContain('<TextLink href="#/settings/memory">Manage in Memory')
  })

  it('the extracted ledger is still MOUNTED by the page (extraction is not deletion)', () => {
    expect(chatPage).toContain('<ContextLedger fed={ledger.fed}')
    expect(chatPage).toContain('learnedOrigin={ledger.learnedOrigin}')
    expect(chatPage).toContain("import { ContextLedger } from './chat/ContextLedger'")
    expect(chatPage).not.toContain('function ContextLedger(')
  })
})
