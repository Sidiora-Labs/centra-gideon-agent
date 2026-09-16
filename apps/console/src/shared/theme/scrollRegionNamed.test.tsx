import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { NativeAgentDetail } from '../../features/agents/AgentDetail'
import type { SavedAgent } from '../data/api'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const agent: SavedAgent = {
  name: 'gideon-loop', provider: 'claude', system_prompt: 'You are gideon-loop. '.repeat(40),
}

describe('the agent system-prompt box is a named region', () => {
  const mount = () => render(
    <NativeAgentDetail agent={agent} isDefault={false} onSaved={vi.fn()} onDeleted={vi.fn()}
      onSetDefault={vi.fn()} editing={false} onEditingChange={vi.fn()} />,
  )

  it('renders the capped scroll box at all — the scan is not vacuous', () => {
    const { container } = mount()
    expect(container.querySelector('.max-h-72.overflow-y-auto'), 'the prompt scroll box').toBeTruthy()
  })

  it('carries the canonical trio: tab stop, group role, explicit name', () => {
    const { container } = mount()
    const box = container.querySelector('.max-h-72.overflow-y-auto')!
    expect(box.getAttribute('tabindex'), 'operable without relying on Chrome auto-focusing scrollers').toBe('0')
    expect(box.getAttribute('role'), 'announced as a labelled container').toBe('group')
    expect(box.getAttribute('aria-label'), 'borrowed from its own Section label').toBe('System prompt')
  })

  it('the explicit name is what stops the content becoming the name', () => {
    const { container } = mount()
    const label = container.querySelector('.max-h-72.overflow-y-auto')!.getAttribute('aria-label')!
    expect(label.length, `an assembled name must stay a name: ${label.slice(0, 60)}`).toBeLessThan(40)
    expect(label).not.toMatch(/You are gideon-loop/)
  })
})

describe('the inbox provenance excerpt is a named region', () => {
  const code = read('features/inbox/InboxDetail.tsx')

  it('the untrusted excerpt scroll box carries the same trio', () => {
    expect(code).toMatch(/<pre tabIndex=\{0\} role="group" aria-label="Why this was proposed" className="mt-1\.5 max-h-40 overflow-auto/)
  })

  it('its name is the disclosure summary, not the excerpt', () => {
    expect(code, 'the summary that owns the word').toMatch(/<summary[^>]*>Why this was proposed<\/summary>/)
    expect(code, 'and the excerpt stays fenced-looking text, not a name')
      .toMatch(/whitespace-pre-wrap[\s\S]{0,120}\{detail\.source_excerpt\}/)
  })
})

describe('the canonical form has one shape across the family', () => {
  const SITES: [string, RegExp][] = [
    ['features/learning/LearningPage.tsx', /tabIndex=\{0\} role="group" aria-label="Capture and proposals"/],
    ['shared/ui/content/ContentSurface.tsx', /tabIndex=\{0\} role="group" aria-label=/],
    ['features/settings/DiagnosticsPanel.tsx', /tabIndex=\{0\} role="group" aria-label="Log output"/],
    ['features/settings/SecurityPanel.tsx', /tabIndex=\{0\} role="group" aria-label=/],
  ]
  for (const [rel, re] of SITES) {
    it(`${rel} still uses the trio this change converged onto`, () => {
      expect(read(rel), 'the canonical form moved — reconcile, do not fork it').toMatch(re)
    })
  }

  it('the kanban rail that established the rule is intact', () => {
    const rail = read('features/tasks/scrollRegionKeyboard.test.tsx')
    expect(rail).toMatch(/column scroll region must own a tab stop/)
    expect(rail, 'and it asserts the same trio').toMatch(/toBe\('group'\)/)
  })
})


describe('the scrollable <pre> family is derived, not hand-listed', () => {
  const SRC = join(process.cwd(), "src")
  const walkTsx = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walkTsx(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  function preTags(src: string): string[] {
    const out: string[] = []
    for (const m of src.matchAll(/<pre\b/g)) {
      let depth = 0
      for (let i = m.index! + m[0].length; i < src.length; i++) {
        const ch = src[i]
        if (ch === '{') depth++
        else if (ch === '}') depth--
        else if (ch === '>' && depth === 0) { out.push(src.slice(m.index!, i + 1)); break }
      }
    }
    return out
  }

  function census() {
    const named: string[] = [], xScroll: string[] = [], yCapped: string[] = []
    for (const abs of walkTsx(SRC)) {
      for (const tag of preTags(readFileSync(abs, 'utf8'))) {
        if (!/overflow/.test(tag)) continue
        const rel = abs.slice(SRC.length + 1)
        if (/tabIndex=\{0\}/.test(tag) && /aria-label/.test(tag)) { named.push(rel); continue }
        if (!/whitespace-pre-wrap/.test(tag)) xScroll.push(rel)
        else if (/max-h-/.test(tag)) yCapped.push(rel)
      }
    }
    return { named, xScroll, yCapped }
  }

  const PENDING = new Set([
    'features/code/DiffReveal.tsx',
    'features/code/TypingReveal.tsx',
    'features/settings/DoctorPanel.tsx',
    'features/settings/DurabilityPanel.tsx',
    'features/skills/SkillInspector.tsx',
  ])

  it('finds the population (not vacuously green)', () => {
    const { named, xScroll, yCapped } = census()
    expect(named.length + xScroll.length + yCapped.length,
      'the <pre> scan must resolve the scrollable boxes').toBeGreaterThanOrEqual(20)
    expect(named.length, 'and the named ones this cycle added').toBeGreaterThanOrEqual(6)
  })

  it('every horizontally-scrolling <pre> is named, or is a listed pending one-off', () => {
    const { xScroll } = census()
    const mute = [...new Set(xScroll)].filter((rel) => !PENDING.has(rel))
    expect(mute, `these scroll sideways and would be announced as their own content:\n${mute.join('\n')}`)
      .toEqual([])
  })

  it('the shared primitives are named — one fix covering every call site', () => {
    const shared: [string, RegExp][] = [
      ['shared/ui/Markdown.tsx', /aria-label="Diff"/],
      ['shared/ui/Markdown.tsx', /aria-label=\{lang \? `\$\{lang\} code` : 'Code'\}/],
      ['shared/ui/widget/MermaidBlock.tsx', /aria-label="Diagram source"/],
      ['shared/ui/ApprovalPrompt.tsx', /aria-label="Tool arguments"/],
      ['features/settings/UpdatesPanel.tsx', /aria-label="Update commands"/],
    ]
    for (const [rel, re] of shared) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, `${rel} must carry its region name`).toMatch(re)
      expect(preTags(src).some((t) => /tabIndex=\{0\}/.test(t) && /role="group"/.test(t)),
        `${rel} must pair the name with tabIndex={0} + role="group"`).toBe(true)
    }
  })

  it('the pending list is not stale — every entry is still unnamed and still x-scrolling', () => {
    const { xScroll } = census()
    const fixed = [...PENDING].filter((rel) => !xScroll.includes(rel))
    expect(fixed, `these are handled now — prune them from PENDING:\n${fixed.join('\n')}`).toEqual([])
  })

  const OVERFLOWS_WITH_REAL_DATA: [string, string][] = [
    ['features/settings/ArchivePanel.tsx', 'Session transcript'],
  ]

  it('a capped box MEASURED to overflow is named — it is no longer latent', () => {
    const { named, yCapped } = census()
    for (const [rel, label] of OVERFLOWS_WITH_REAL_DATA) {
      expect(named, `${rel} overflows with real data, so it needs the trio`).toContain(rel)
      expect(yCapped, `${rel} must not slip back into the un-asserted latent bucket`).not.toContain(rel)
      expect(readFileSync(join(SRC, rel), 'utf8'), `${rel}'s region name`)
        .toMatch(new RegExp(`aria-label="${label}"`))
      expect(label.length, 'a name must stay a name, not become the content').toBeLessThan(40)
    }
  })

  it('the latent bucket is still being collected — the check above is not measuring an empty set', () => {
    const { yCapped } = census()
    expect(yCapped.length, 'capped whitespace-pre-wrap boxes still exist and are still unasserted')
      .toBeGreaterThan(0)
  })
})
