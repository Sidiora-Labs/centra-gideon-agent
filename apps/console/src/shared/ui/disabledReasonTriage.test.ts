import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { controlAvailability, activateControl } from './controlState'
import { unavailableWhen, BUSY_REASON } from './unavailable'
import { jsxTags } from '../testing/jsxContracts'


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const BUSY = /\b(busy|saving|sending|loading|installing|retrying|pending|working|submitting|launching|testing|promoting|consolidating|regen\w*|bulkBusy|levelBusy|deleting|creating|running|uploading|importing|exporting|refreshing|syncing|starting|stopping)\b/i

const tagCache = new Map<string, ReturnType<typeof jsxTags>>()
function buttonTags(src: string) {
  let sites = tagCache.get(src)
  if (!sites) {
    sites = jsxTags(src, ['Button'])
    tagCache.set(src, sites)
  }
  return sites
}

// These are forwarding props and one uninitialized editor, not file-wide exceptions.
const classifiedGate = (rel: string, gate: string) =>
  (gate === '{disabled}' && ['features/settings/DurabilityPanel.tsx', 'features/settings/ProjectionRulesPanel.tsx'].includes(rel)) ||
  (rel === 'features/skills/SkillInspector.tsx' && gate === '{busy || content === null}')
const explained = (site: ReturnType<typeof jsxTags>[number]) =>
  site.attributes.has('disabledReason') ||
  (site.attributes.has('aria-description') && site.attributes.has('title'))

const offenders = walk(SRC).flatMap((f) => {
  const rel = f.slice(SRC.length + 1)
  return buttonTags(readFileSync(f, 'utf8'))
    .filter(site => site.attributes.has('disabled') && !explained(site))
    .filter(site => {
      const gate = site.attributes.get('disabled')!
      if (classifiedGate(rel, gate) || gate === site.attributes.get('loading')) return false
      if (rel === 'features/capabilities/experience/AmbientDisplay.tsx' && gate === '{fullscreen}' &&
        site.element.includes("fullscreen ? 'Fullscreen active' : 'Enter fullscreen'")) return false
      return gate.slice(1, -1).split(/\|\||&&/).map(s => s.trim()).filter(Boolean).some(c => !BUSY.test(c))
    })
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
    expect(unavailableWhen(true, 'Choose a project', { busy: true }))
      .toEqual({ disabled: true, 'aria-busy': true, title: undefined })
    const raw = unavailableWhen(true, 'Choose a project')
    expect(raw['aria-disabled']).toBe(true)
    expect(raw.disabled).toBeUndefined()
    let stopped = false
    raw.onClickCapture?.({ preventDefault: () => { prevented = true }, stopPropagation: () => { stopped = true } } as React.MouseEvent)
    expect(stopped).toBe(true)

  })

  it('the in-flight class now EXPLAINS itself, and shares one sentence to do it', () => {
    const inbox = readFileSync(join(SRC, 'features/inbox/InboxDetail.tsx'), 'utf8')
    const busyTags = buttonTags(inbox).filter(({ tag }) => /disabled=\{!!busy\}/.test(tag))
    expect(busyTags.length, 'the inbox action rows are the canonical busy-only case').toBeGreaterThanOrEqual(4)
    expect(
      busyTags.filter(({ tag }) => !/disabledReason=/.test(tag) && !/\bloading=/.test(tag)),
      'a busy gate owes a reason now: soft-off keeps the tab stop AND refuses the click',
    ).toEqual([])
    expect(BUSY_REASON).not.toMatch(/\b(another|other|sibling)\b/i)

  })

  it('🔴 THE RATCHET: no busy-gated Button may go back to explaining nothing', () => {
    const silent = walk(SRC).flatMap((f) => {
      const rel = f.slice(SRC.length + 1)
      return buttonTags(readFileSync(f, 'utf8'))
        .filter(site => site.attributes.has('disabled') && !explained(site) && !site.attributes.has('loading'))
        .filter(site => {
          const gate = site.attributes.get('disabled')!
          // ReadingView's pending value is the selected text, not an operation.
          if (rel === 'features/knowledge/ReadingView.tsx' && gate === '{!pending}') return false
          return BUSY.test(gate)
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
