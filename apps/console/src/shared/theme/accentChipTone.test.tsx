import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { RungChip } from '../ui/RungChip'
import { RUNG_PRESENTATION } from '../data/rungs'
import { accentChip, toneChipSkin } from './accent'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })

const rung = (resolved: string) =>
  ({
    key: 'test.action',
    floor: 'draft_only',
    ceiling: 'autonomous',
    leaves_machine: false,
    providers: ['p'],
    resolved_rung: resolved,
    granted_rung: resolved,
    held_by_incident: false,
    authority: 'declared floor',
    granted_at: '',
  }) as never

const chipStyle = (resolved: string) => {
  const { unmount } = render(<RungChip type={rung(resolved)} />)
  const el = screen.getByTitle(/declared floor/)
  const style = el.getAttribute('style') ?? ''
  unmount()
  return style
}

describe('a rung chip inks coral through the container pair, not a tint of itself', () => {
  it('the coral rung uses the shipped accent pair', () => {
    const style = chipStyle('autonomous')
    expect(style, 'the opaque container fill').toContain('var(--color-primary-container)')
    expect(style, 'and its guaranteed ink').toContain('var(--color-on-primary-container)')
    expect(style, 'no tint of the accent may remain — that is the 3.97:1 pairing')
      .not.toMatch(/color-mix/)
  })

  it('it is literally the shared pair, so it cannot drift from the other adopters', () => {
    const style = chipStyle('autonomous')
    expect(style).toContain(accentChip.background)
    expect(style).toContain(accentChip.color)
  })

  it('the three passing rungs keep their tint, each with its own tone', () => {
    for (const [resolved, tone] of [
      ['draft_only', 'var(--color-on-surface-low)'],
      ['one_tap', 'var(--color-on-surface-var)'],
      ['auto_with_undo', 'var(--color-info)'],
    ] as const) {
      const style = chipStyle(resolved)
      expect(style, `${resolved} keeps the 14% tint`).toContain('color-mix')
      expect(style, `${resolved} tints with its OWN tone`).toContain(tone)
      expect(style, `${resolved} must not borrow the coral container`)
        .not.toContain('primary-container')
    }
  })

  it('the remap is not vacuous — the registry still declares exactly one coral rung', () => {
    const coral = Object.entries(RUNG_PRESENTATION).filter(([, p]) => p.tone === 'var(--color-primary)')
    expect(coral.map(([k]) => k)).toEqual(['autonomous'])
  })

  it('every consumer gets the fix from the primitive, so none can be missed', () => {
    const users = walk(SRC).filter((abs) => /<RungChip\b/.test(readFileSync(abs, 'utf8')))
    expect(users.map((a) => a.slice(SRC.length + 1)).sort()).toEqual([
      'features/chat/ApprovalCard.tsx',
      'features/companion/CompanionPage.tsx',
      'features/settings/GuardrailsPanel.tsx',
      'features/triggers/TriggersListPage.tsx',
    ])
  })

  it("the family's rail points here, so the third spelling is findable from it", () => {
    expect(readFileSync(join(SRC, 'shared/theme/accentChip.test.ts'), 'utf8'))
      .toMatch(/accentChipTone\.test\.tsx/)
  })
})


describe('toneChipSkin is the one rule the tone-registry chips share', () => {
  it('routes coral to the container pair, with no tint left to fail', () => {
    const skin = toneChipSkin('var(--color-primary)', 14)
    expect(skin).toEqual({ ...accentChip })
    expect(JSON.stringify(skin)).not.toMatch(/color-mix|%/)
  })

  it('leaves every passing tone on its own tint, at the caller\'s strength', () => {
    for (const tone of ['var(--color-info)', 'var(--color-ok)', 'var(--color-warn)', 'var(--color-danger)',
                        'var(--color-on-surface-low)', 'var(--color-on-surface-var)']) {
      expect(toneChipSkin(tone, 16)).toEqual({
        background: `color-mix(in srgb, ${tone} 16%, transparent)`, color: tone,
      })
    }
  })

  it('honours each adopter\'s own strength rather than unifying them', () => {
    expect(toneChipSkin('var(--color-info)', 14).background).toContain('14%')
    expect(toneChipSkin('var(--color-info)', 16).background).toContain('16%')
    expect(toneChipSkin('var(--color-primary)', 20).background).toBe(accentChip.background)
  })

  it('every adopter goes through it, so none can re-decide the rule', () => {
    const rung = read('shared/ui/RungChip.tsx')
    expect(rung).toMatch(/toneChipSkin\(meta\.tone, 14\)/)
    expect(rung, 'the inline ternary it replaced must be gone').not.toMatch(/\? accentChip\s*\n/)
    const notif = read('features/notifications/NotificationsPage.tsx')
    expect(notif, 'the LABELLED kind chip').toMatch(/style=\{toneChipSkin\(km\.tone, 16\)\}/)
    const sched = read('features/schedule/ScheduleDetail.tsx')
    expect(sched, 'the schedule-kind chip').toMatch(/style=\{toneChipSkin\(km\.tone, 16\)\}/)
    expect(sched, 'the exec-mode chip').toMatch(/style=\{toneChipSkin\(mm\.tone, 16\)\}/)
    expect(sched, 'no raw tint of a tone may remain on this surface')
      .not.toMatch(/color-mix\(in srgb, \$\{(?:km|mm)\.tone\}/)
  })

  it('the prompt SOURCE pills go through it too — the default source is coral', () => {
    for (const rel of ['features/prompts/PromptDetail.tsx', 'features/prompts/SnippetDetail.tsx']) {
      const code = read(rel)
      expect(code, `${rel} source pill`).toMatch(/style=\{toneChipSkin\(sourceTone\(\w+\.source\), 16\)\}/)
      expect(code, `${rel} must keep no raw tint of the tone`)
        .not.toMatch(/color-mix\(in srgb, \$\{sourceTone\(/)
    }
  })

  it('the prompt tone function still returns coral for the default source', () => {
    const meta = read('features/prompts/promptMeta.ts')
    expect(meta).toMatch(/if \(!source \|\| source === 'user'\) return 'var\(--color-primary\)'/)
    const coralReturns = [...meta.matchAll(/return 'var\(--color-primary\)'/g)]
    expect(coralReturns, 'exactly one coral branch in sourceTone').toHaveLength(1)
  })

  it('the icon-only and accent-BAR uses of the same tone are untouched', () => {
    const list = read('features/prompts/PromptsListPage.tsx')
    expect(list, 'the square icon tile keeps its plain tint')
      .toMatch(/size-10 items-center justify-center rounded-lg" style=\{\{ background: `color-mix\(in srgb, \$\{sourceTone\(r\.source\)\} 16%, transparent\)` \}\}/)
    expect(list).toMatch(/accent=\{sourceTone\(r\.source\)\}/)
  })

  it('the last two coral-capable registry chips go through it too', () => {
    const inbox = read('features/inbox/InboxDetail.tsx')
    expect(inbox, 'the kind chip').toMatch(/style=\{toneChipSkin\(km\.tone, 16\)\}/)
    expect(inbox).not.toMatch(/color-mix\(in srgb, \$\{km\.tone\}/)
    const agent = read('features/agents/AgentDetail.tsx')
    expect(agent, 'the provider chip').toMatch(/style=\{toneChipSkin\(pm\.tone, 16\)\}/)
    expect(agent).not.toMatch(/color-mix\(in srgb, \$\{pm\.tone\}/)
  })

  it('only the coral-capable registries were touched — the census that removed work', () => {
    expect(read('features/tasks/taskMeta.tsx'), 'no coral in the task registry')
      .not.toMatch(/var\(--color-primary\)/)
    const inboxMeta = read('features/inbox/inboxMeta.ts')
    const coralKinds = [...inboxMeta.matchAll(/key: '(\w+)', label: '[^']*', tone: 'var\(--color-primary\)'/g)].map((m) => m[1])
    expect(coralKinds).toEqual(['message', 'mention', 'email'])
    const agentMeta = read('features/agents/agentMeta.ts')
    expect([...agentMeta.matchAll(/return \{ label: '[^']*', icon: \w+, tone: 'var\(--color-primary\)' \}/g)]).toHaveLength(1)
  })

  it('the schedule registry still has exactly the two coral tones this covers', () => {
    const meta = read('features/schedule/scheduleMeta.ts')
    const coral = [...meta.matchAll(/key: '(\w+)'[^}]*tone: 'var\(--color-primary\)'/g)].map((m) => m[1])
    expect(coral.sort()).toEqual(['agent', 'cron'])
  })

  it('the icon-only tiles keep the plain tint — the distinction, not an oversight', () => {
    const notif = read('features/notifications/NotificationsPage.tsx')
    expect(notif).toMatch(/style=\{\{ background: toneChipBg\(km\.tone\) \}\}><km\.icon/)
    expect(read('shared/ui/NotificationBell.tsx')).toMatch(/background: toneChipBg\(km\.tone\)/)
  })
})


describe('the skill source chip routes its registry tone through the helper', () => {
  const LIST = read('features/skills/SkillsPage.tsx')
  const INSPECTOR = read('features/skills/SkillInspector.tsx')

  it('both call sites use toneChipSkin, at their own strengths', () => {
    expect(LIST).toMatch(/style=\{toneChipSkin\(tone\)\}/)
    expect(INSPECTOR).toMatch(/style=\{toneChipSkin\(tone, 16\)\}/)
  })

  it('neither hand-rolls the tint any more', () => {
    const strip = (x: string) => x.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')
    for (const [name, src] of [['SkillsPage', LIST], ['SkillInspector', INSPECTOR]] as const)
      expect(strip(src), `${name} raw tone tint`)
        .not.toMatch(/background: `color-mix\(in srgb, \$\{tone\} \d+%, transparent\)`, color: tone/)
  })

  it('the registry still reaches coral — the vacuity floor', () => {
    const meta = read('features/skills/skillMeta.ts')
    const coral = [...meta.matchAll(/^\s*'?([\w.-]+)'?: 'var\(--color-primary\)'/gm)].map((m) => m[1])
    expect(coral, 'sources whose tone is coral').toEqual(['bundled', 'native'])
  })

  it("the inspector's semantic sibling keeps its raw tint", () => {
    expect(INSPECTOR).toMatch(/background: 'color-mix\(in srgb, var\(--color-warn\) 16%, transparent\)', color: 'var\(--color-warn\)'/)
  })

  it('the call site carries the measurement, not just the token', () => {
    expect(LIST).toMatch(/3\.97:1/)
  })
})
