import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { SegToggle } from './bento'
import { SegPills } from './settingsUI'


const SETTINGS = join(process.cwd(), "src/features/settings")
const SRC = join(process.cwd(), "src")

const stripComments = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

function tags(src: string, name: string): string[] {
  const out: string[] = []
  for (const m of src.matchAll(new RegExp(`<${name}\\b`, 'g'))) {
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

describe('SegToggle announces its dimension and its state', () => {
  it('names each option <dimension>: <value>', () => {
    render(<SegToggle ariaLabel="Mode" value="dark" onPick={vi.fn()}
      options={[{ key: 'light', label: 'Light' }, { key: 'dark', label: 'Dark' }, { key: 'auto', label: 'Auto' }]} />)
    expect(screen.getByRole('button', { name: 'Mode: Light' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Mode: Dark' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Mode: Auto' })).toBeTruthy()
  })

  it('marks exactly the active option as pressed', () => {
    render(<SegToggle ariaLabel="Density" value="less" onPick={vi.fn()}
      options={[{ key: 'more', label: 'Comfortable' }, { key: 'less', label: 'Compact' }]} />)
    expect(screen.getByRole('button', { name: 'Density: Compact' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'Density: Comfortable' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('leaves the VISIBLE label alone — this is a naming fix, not a redesign', () => {
    const { container } = render(<SegToggle ariaLabel="Min severity" value="info" onPick={vi.fn()}
      options={[{ key: 'info', label: 'All' }, { key: 'error', label: 'Errors' }]} />)
    expect([...container.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['All', 'Errors'])
  })
})

describe('every SegToggle call site names its dimension', () => {
  const sites = walk(SRC).flatMap((f) => tags(stripComments(readFileSync(f, 'utf8')), 'SegToggle').map((t) => ({ f, t })))

  it('finds the call sites (not vacuously green)', () => {
    expect(sites.length, 'the matcher must find the SegToggle call sites').toBeGreaterThanOrEqual(3)
  })

  it('has no unnamed call site', () => {
    const mute = sites.filter((s) => !/\bariaLabel=/.test(s.t))
    expect(mute.map((s) => s.f), 'SegToggle without a dimension').toEqual([])
  })
})

describe("the Design panel's hand-rolled mode pills agree with the primitive", () => {
  const src = stripComments(readFileSync(join(SETTINGS, 'DesignPanel.tsx'), 'utf8'))

  it('announces Mode: <value> and its pressed state', () => {
    expect(src).toMatch(/aria-label=\{`Mode: \$\{m\.label\}`\}/)
    expect(src).toMatch(/aria-pressed=\{on\}/)
  })

  it('never uses the saved-scheme word for the light/dark axis', () => {
    const paired = [/\b(system|light|dark)\b[^\n]{0,20}\btheme\b/i, /<\/strong>\s*theme\b/i, /\btheme\b[^\n]{0,20}\b(you are|currently|system)\b/i]
      .filter((re) => re.test(src)).map((re) => String(re))
    expect(paired, 'a user-visible string pairs the saved-scheme word with the light/dark axis').toEqual([])

    const mentions = (src.match(/\btheme(s)?\b/gi) || []).length
    expect(mentions, 'the saved-scheme copy must still be there (else this passes vacuously)').toBeGreaterThanOrEqual(8)
  })

  it('spells color the way the other 1600 sites do', () => {
    expect(src).not.toMatch(/colour/i)
  })
})

describe('SegPills announces its dimension and its state', () => {
  it('names each option <dimension>: <value>', () => {
    render(<SegPills ariaLabel="Widget density" value="more" onChange={vi.fn()}
      options={[{ key: 'more', label: 'More' }, { key: 'less', label: 'Less' }]} />)
    expect(screen.getByRole('button', { name: 'Widget density: More' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Widget density: Less' })).toBeTruthy()
  })

  it('marks exactly the active option as pressed', () => {
    render(<SegPills ariaLabel="Scan mode" value="redact" onChange={vi.fn()}
      options={[{ key: 'warn', label: 'Warn' }, { key: 'redact', label: 'Redact' }, { key: 'block', label: 'Block' }]} />)
    expect(screen.getByRole('button', { name: 'Scan mode: Redact' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'Scan mode: Warn' }).getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('button', { name: 'Scan mode: Block' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('leaves the VISIBLE label alone — a naming fix, not a redesign', () => {
    const { container } = render(<SegPills ariaLabel="Restore window" value="30" onChange={vi.fn()}
      options={[{ key: '15', label: '15 min' }, { key: '30', label: '30 min' }]} />)
    expect([...container.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['15 min', '30 min'])
  })
})

describe('every SegPills call site names its dimension', () => {
  const sites = walk(SRC).flatMap((f) => tags(stripComments(readFileSync(f, 'utf8')), 'SegPills').map((t) => ({ f, t })))

  it('finds the call sites (not vacuously green)', () => {
    expect(sites.length, 'the matcher must find the SegPills call sites').toBeGreaterThanOrEqual(8)
  })

  it('has no unnamed call site', () => {
    const mute = sites.filter((s) => !/\bariaLabel=/.test(s.t))
    expect(mute.map((s) => s.f), 'SegPills without a dimension').toEqual([])
  })

  it("the 26-at-once matrix names the RULE, not just the dimension", () => {
    const src = stripComments(readFileSync(join(SETTINGS, 'NotificationRulesMatrix.tsx'), 'utf8'))
    expect(src).toMatch(/ariaLabel=\{`Delivery mode for \$\{r\.label\}`\}/)
  })
})

describe('the family is DERIVED, so the next pill group cannot be missed', () => {
  const DELEGATES = ['TileButton', 'SegToggle', 'SegPills', 'Segmented', 'WidthPill', 'MenuRow']

  function exclusiveGroups() {
    const LITERAL = /^(?:true|false|null|undefined|\d+)$/
    const STATE = /aria-pressed|aria-selected|aria-checked|aria-current|aria-expanded|role="(?:tab|radio|option|menuitemradio|treeitem)"/
    const out: { rel: string; state: boolean; cmp: string; via: 'equality' | 'membership' }[] = []
    for (const f of walk(SRC)) {
      const src = stripComments(readFileSync(f, 'utf8'))
      for (const m of src.matchAll(/\.map\(\s*\(?\s*(\w+)[^)]{0,40}\)?\s*=>\s*\{/g)) {
        const body = src.slice(m.index!, m.index! + 1100)
        const item = m[1]
        const eq = body.match(new RegExp(`const \\w+ = (?:${item}(?:\\.\\w+)? === (\\w+)|(\\w+) === ${item}(?:\\.\\w+)?)`))
        const member = body.match(new RegExp(
          `const \\w+ = !?\\w+\\.(?:includes|has)\\(\\s*${item}(?:\\.\\w+)?\\s*\\)`
          + `|const \\w+ = \\w+\\.indexOf\\(\\s*${item}(?:\\.\\w+)?\\s*\\)\\s*(?:>= 0|!== -1)`))
        const cmp = eq ?? member
        if (!cmp) continue
        if (eq && LITERAL.test(eq[1] ?? eq[2] ?? '')) continue
        const rows = [...tags(body, 'button'), ...tags(body, 'motion\\.button')]
        const delegates = DELEGATES.some((c) => new RegExp(`<${c}\\b`).test(body))
        if (rows.length === 0 && !delegates) continue
        out.push({
          rel: f.slice(SRC.length + 1),
          state: delegates || rows.some((t) => STATE.test(t)),
          cmp: cmp[0],
          via: eq ? 'equality' : 'membership',
        })
      }
    }
    return out
  }

  const PENDING = new Set<string>([])

  it('every exclusive-choice group marks its state, or is a named exception', () => {
    const groups = exclusiveGroups()
    expect(groups.length, 'the sweep must find the groups').toBeGreaterThanOrEqual(23)
    for (const rel of ['features/settings/NotificationRulesMatrix.tsx', 'features/settings/PersonalityPicker.tsx']) {
      expect(groups.map((g) => g.rel), `${rel} delegates, and must still be COUNTED`).toContain(rel)
    }
    expect(groups.some((g) => g.rel === 'features/settings/settingsUI.tsx'), 'SegPills must be in scope').toBe(true)
    expect(groups.filter((g) => g.rel === 'features/settings/MemoryPanel.tsx').length,
      'the three the name-keyed sweep could not see').toBeGreaterThanOrEqual(3)
    expect(groups.filter((g) => g.rel === 'features/settings/DiagnosticsPanel.tsx').length,
      'both level pickers, not one').toBeGreaterThanOrEqual(2)

    const mute = groups.filter((g) => !g.state && !PENDING.has(g.rel)).map((g) => g.rel)
    expect(mute, `these convey selection visually only:\n${mute.join('\n')}`).toEqual([])
  })

  it('the membership spelling is swept, and every one of its groups is marked', () => {
    const groups = exclusiveGroups()
    const member = groups.filter((g) => g.via === 'membership')
    expect(member.length, 'the membership matcher must resolve its own population').toBeGreaterThanOrEqual(6)
    for (const rel of [
      'features/settings/SearchPanel.tsx',
      'features/ChatPage.tsx',
      'features/agents/AgentForm.tsx',
      'features/loops/LoopPlanReview.tsx',
    ]) {
      expect(member.map((g) => g.rel), `${rel} holds its selection in a collection — it must be COUNTED`)
        .toContain(rel)
    }
    const mute = member.filter((g) => !g.state).map((g) => `${g.rel}  ${g.cmp}`)
    expect(mute, `these hold selection in a collection and announce nothing:\n${mute.join('\n')}`).toEqual([])
  })

  it('each of those groups also states its DIMENSION, not just its state', () => {
    const named: [string, RegExp][] = [
      ['features/settings/SearchPanel.tsx', /role="group" aria-label=\{`\$\{meta\.label\} provider`\}/],
      ['features/ChatPage.tsx', /role="group" aria-label="Knowledge to attach"/],
      ['features/ChatPage.tsx', /role="group" aria-label="Filter by tag"/],
      ['features/agents/AgentForm.tsx', /role="group" aria-label=\{label\}/],
      ['features/loops/LoopPlanReview.tsx', /role="group" aria-label=\{label\}/],
    ]
    for (const [rel, re] of named) {
      const src = stripComments(readFileSync(join(SRC, rel), 'utf8'))
      expect(src, `${rel} must name the group its options belong to`).toMatch(re)
    }
  })

  it('the pending list is not stale — every entry is still unmarked', () => {
    const groups = exclusiveGroups()
    const fixed = [...PENDING].filter((rel) => {
      const mine = groups.filter((g) => g.rel === rel)
      return mine.length > 0 && mine.every((g) => g.state)
    })
    expect(fixed, `these are marked now — prune them from PENDING:\n${fixed.join('\n')}`).toEqual([])
  })

  it("the settings panels that had no selection state now have it", () => {
    const groups = exclusiveGroups()
    for (const rel of ['features/settings/MemoryPanel.tsx', 'features/settings/DiagnosticsPanel.tsx']) {
      const mine = groups.filter((g) => g.rel === rel)
      expect(mine.length, `${rel} must still be in scope`).toBeGreaterThan(0)
      const mute = mine.filter((g) => !g.state).map((g) => g.cmp)
      expect(mute, `${rel} still conveys selection visually only:\n${mute.join('\n')}`).toEqual([])
    }
  })
})

describe('the last two current-item markers in the tree', () => {
  const codeOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it("the task-list pill says which list the page is showing", () => {
    const code = codeOf('features/tasks/TasksListPage.tsx')
    expect(code).toMatch(/aria-label=\{`Task list: \$\{l\.name\}`\} aria-pressed=\{isActive\}/)
    expect(code, 'and the Reset button keeps its own separate name')
      .toMatch(/aria-label=\{`Reset list \$\{l\.name\}`\}/)
  })

  it("the file tree says which file is open, and only where the tint claims it", () => {
    const code = codeOf('features/files/browse/FileTree.tsx')
    expect(code).toMatch(/aria-current=\{isActive \? 'page' : undefined\}/)
    expect(code, 'the row still declares folder expansion separately')
      .toMatch(/aria-expanded=\{entry\.is_dir \? open : undefined\}/)
    expect(code).toMatch(/isActive \? 'color-mix\(in srgb, var\(--color-primary\) 14%, transparent\)'/)
  })
})

describe('delegation is a real path, not a hole in the sweep', () => {
  const codeOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  const DECLARED_IN: Record<string, string> = {
    TileButton: 'shared/ui/TileButton.tsx',
    SegToggle: 'features/settings/bento.tsx',
    SegPills: 'features/settings/settingsUI.tsx',
    Segmented: 'shared/ui/Segmented.tsx',
    WidthPill: 'shared/ui/WidthPill.tsx',
    MenuRow: 'shared/ui/Popover.tsx',
  }

  it('every delegate declares a selection state for its caller', () => {
    for (const [name, rel] of Object.entries(DECLARED_IN)) {
      const src = codeOf(rel)
      expect(src, `${name} (${rel}) must declare a selection state`)
        .toMatch(/aria-pressed|aria-selected|aria-checked|aria-current|role="(?:tab|option|radio|menuitemradio)"/)
    }
  })

  it("the delegate list and the list the sweep uses are the same list", () => {
    const self = readFileSync(join(SRC, 'features/settings/exclusiveChoiceNamed.test.tsx'), 'utf8')
    const declared = self.match(/const DELEGATES = \[([^\]]+)\]/)
    expect(declared, "the sweep's DELEGATES array must be findable").toBeTruthy()
    const names = [...declared![1].matchAll(/'([A-Za-z]+)'/g)].map((m) => m[1]).sort()
    expect(names, 'every delegate must be proven above').toEqual(Object.keys(DECLARED_IN).sort())
  })

  it("PersonalityPicker is the case that proved delegation — and it is NOT a defect", () => {
    const picker = stripComments(codeOf('features/settings/PersonalityPicker.tsx'))
    const tile = tags(picker, 'TileButton')
    expect(tile.length, 'the picker must still render TileButton').toBeGreaterThan(0)
    expect(tile.some((t) => /active=\{active\}/.test(t)), 'and hand it the selection').toBe(true)
    expect(codeOf('shared/ui/TileButton.tsx'), 'and the primitive is where the state lives')
      .toMatch(/aria-pressed=\{active\}/)
  })
})
