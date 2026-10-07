import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { controlAvailability, activateControl } from './controlState'
import { jsxTags } from '../testing/jsxContracts'


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const BUSY = /\b(busy|saving|sending|loading|installing|retrying|pending|working|submitting|launching|testing|promoting|consolidating|regen\w*|bulkBusy|levelBusy|deleting|creating|running|uploading|importing|exporting|refreshing|syncing|starting|stopping)\b/i

function buttonTags(src: string): Array<{ tag: string; line: number }> {
  return jsxTags(src, ['Button']).map(({ tag, line }) => ({ tag, line }))
}

const EXEMPT: Record<string, string> = {
  'features/loops/DesignCockpitPage.tsx': 'the gate is a pass-through `disabled` prop; the reason belongs to the caller',
  'features/settings/DurabilityPanel.tsx': 'the gate is a pass-through `disabled` prop; the reason belongs to the caller',
  'features/settings/ProjectionRulesPanel.tsx': 'the gate is a pass-through `disabled` prop; the reason belongs to the caller',
  'features/schedule/ScheduleDetail.tsx': '`ranFlash` is a transient post-run flash — in-flight, not blocked',
  'features/skills/SkillInspector.tsx': '`content === null` means still loading',
}

const offenders = walk(SRC).flatMap((f) => {
  const rel = f.slice(SRC.length + 1)
  const src = readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  return buttonTags(src)
    .filter(({ tag }) => /\bdisabled=\{/.test(tag) && !/\bdisabledReason=/.test(tag))
    .filter(({ tag }) => {
      const gate = /\bdisabled=\{([^}]*(?:\{[^}]*\}[^}]*)*)\}/.exec(tag)?.[1] ?? ''
      return gate.split(/\|\||&&/).map((s) => s.trim()).filter(Boolean).some((c) => !BUSY.test(c))
    })
    .filter(() => !(rel in EXEMPT))
    .map(({ line }) => `${rel}:${line}`)
})

describe('a disabled Button that a user could unblock says how', () => {
  it('reads only real JSX props, including nested callbacks and strings', () => {
    const sites = jsxTags(`// <Button disabled={fake} />
<Button aria-disabled={true} title="https://example.test/a" onClick={() => a < b} disabledReason={missing ? 'Choose > one' : undefined} />`, ['Button'])
    expect(sites).toHaveLength(1)
    expect(sites[0].attributes.has('disabled')).toBe(false)
    expect(sites[0].attributes.get('aria-disabled')).toBe('{true}')
    expect(sites[0].attributes.get('disabledReason')).toBe("{missing ? 'Choose > one' : undefined}")
  })

  it('finds the population (not vacuously green)', () => {
    const all = walk(SRC).flatMap((f) => buttonTags(readFileSync(f, 'utf8')).filter(({ tag }) => /\bdisabled=\{/.test(tag)))
    expect(all.length, 'the matcher must find the disabled Buttons').toBeGreaterThanOrEqual(100)
    const withReason = walk(SRC).flatMap((f) => buttonTags(readFileSync(f, 'utf8')).filter(({ tag }) => /\bdisabledReason=/.test(tag)))
    expect(withReason.length, 'and the ones that explain themselves').toBeGreaterThanOrEqual(48)
  })

  it('has none left unexplained', () => {
    expect(offenders, 'a keyboard user tabs past this action and cannot learn what is missing').toEqual([])
  })

  it('🔴 THE PREMISE: a soft-off Button REFUSES the click, which is what makes a busy reason safe', () => {
    const btn = readFileSync(join(SRC, 'shared/ui/Button.tsx'), 'utf8')
    expect(btn).toMatch(/controlAvailability\(disabled, loading, disabledReason\)/)
    expect(btn).toMatch(/activateControl\(event, state.blocked, onClick\)/)
    const state = controlAvailability(true, false, 'Choose a project')
    expect(state).toEqual({ blocked: true, nativeDisabled: false, ariaDisabled: true, busy: undefined })
    let prevented = false
    let called = false
    const event = { preventDefault: () => { prevented = true } } as Parameters<typeof activateControl>[0]
    activateControl(event, state.blocked, () => { called = true })
    expect(prevented).toBe(true)
    expect(called).toBe(false)
    const un = readFileSync(join(SRC, 'shared/ui/unavailable.ts'), 'utf8')
    expect(un, "a raw <button> has no click guard, so its busy branch keeps the native attribute")
      .toMatch(/if \(opts\?\.busy\) return \{ disabled: true, 'aria-busy': true, title: opts\.title \}/)
  })

  it('the in-flight class now EXPLAINS itself, and shares one sentence to do it', () => {
    const inbox = readFileSync(join(SRC, 'features/inbox/InboxDetail.tsx'), 'utf8')
    const busyTags = buttonTags(inbox).filter(({ tag }) => /disabled=\{!!busy\}/.test(tag))
    expect(busyTags.length, 'the inbox action rows are the canonical busy-only case').toBeGreaterThanOrEqual(4)
    expect(
      busyTags.filter(({ tag }) => !/disabledReason=/.test(tag) && !/\bloading=/.test(tag)),
      'a busy gate owes a reason now: soft-off keeps the tab stop AND refuses the click',
    ).toEqual([])
    const un = readFileSync(join(SRC, 'shared/ui/unavailable.ts'), 'utf8')
    expect(un, 'the shared sentence lives in one place').toMatch(/export const BUSY_REASON = /)
    expect(un.match(/export const BUSY_REASON = '([^']+)'/)?.[1], 'neutral about the owner')
      .not.toMatch(/\b(another|other|sibling)\b/i)
  })

  it('🔴 THE RATCHET: no busy-gated Button may go back to explaining nothing', () => {
    const OVERLOADED_VOCABULARY: Record<string, string> = {
      'features/knowledge/ReadingView.tsx':
        '`!pending` is a pending text SELECTION, not an in-flight action — matched by the word, not the meaning',
    }
    const silent = walk(SRC).flatMap((f) => {
      const rel = f.slice(SRC.length + 1)
      if (rel in OVERLOADED_VOCABULARY) return []
      const src = readFileSync(f, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
        .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
        .replace(/(^|[^:"'`])\/\/[^\n]*/g, (m, p1) => p1 + ' '.repeat(m.length - p1.length))
      return buttonTags(src)
        .filter(({ tag }) => /\bdisabled=\{/.test(tag) && !/\bdisabledReason=/.test(tag) && !/\bloading=/.test(tag))
        .filter(({ tag }) => {
          const gate = /(?<!aria-)\bdisabled=\{([^}]*(?:\{[^}]*\}[^}]*)*)\}/.exec(tag)?.[1] ?? ''
          return !!gate && BUSY.test(gate)
        })
        .map(({ line }) => `${rel}:${line}`)
    })
    expect(silent, 'a busy-gated Button with neither `loading=` nor a reason explains nothing to anyone')
      .toEqual([])
    const withReason = walk(SRC).flatMap((f) =>
      buttonTags(readFileSync(f, 'utf8')).filter(({ tag }) => /\bdisabledReason=\{BUSY_REASON\}/.test(tag)))
    expect(withReason.length, 'the shared reason must still be in use').toBeGreaterThanOrEqual(40)
  })

  it('never parks the reason on a wrapper the keyboard user cannot reach', () => {
    const parked = walk(SRC).flatMap((f) => {
      const src = readFileSync(f, 'utf8')
      return [...src.matchAll(/<(span|div)[^>]{0,200}?\btitle=[^>]{0,240}>\s*\n?\s*<Button\b[^>]{0,400}?disabled=/gs)]
        .filter((m) => !/Dry-run replay/.test(m[0]))
        .map(() => f.slice(SRC.length + 1))
    })
    expect(parked, 'a wrapper title is a hover tooltip; a natively disabled button inside it is unreachable').toEqual([])
  })
})
