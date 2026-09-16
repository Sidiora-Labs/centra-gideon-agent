import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { Field as FormsField } from './forms'
import { Toggle } from './Toggle'
import { Combobox } from './Combobox'
import { ShortcutRecorder } from './ShortcutRecorder'
import { Row } from '../../features/settings/settingsUI'


function describedText(el: Element | null, scope: HTMLElement): string {
  if (!el) return ''
  const ids = el.getAttribute('aria-describedby')
  if (ids) {
    const parts = ids.split(/\s+/).filter(Boolean).map((id) => {
      const target = scope.querySelector(`[id="${CSS.escape(id)}"]`)
      return target ? (target.textContent || '').replace(/\s+/g, ' ').trim() : `DANGLING:${id}`
    })
    const joined = parts.join(' ').trim()
    if (joined) return joined
  }
  return (el.getAttribute('title') || '').replace(/\s+/g, ' ').trim()
}

const HINT = 'Display a time on each message.'
const REASON = 'Set a password first — a sign-in form nobody can pass is worse than none'

describe('Toggle claims the hint published beside it', () => {
  it('a switch in a hinted Row is DESCRIBED by that row’s sentence', () => {
    const { container } = render(
      <Row label="Timestamps" hint={HINT}><Toggle on={false} onChange={() => {}} label="Timestamps" /></Row>,
    )
    const sw = container.querySelector('[role="switch"]')
    expect(sw).not.toBeNull()
    expect(describedText(sw, container)).toBe(HINT)
    expect(sw!.getAttribute('aria-label')).toBe('Timestamps')
  })

  it('a switch in a hinted ui/forms Field is described too (the other publisher)', () => {
    const { container } = render(
      <FormsField label="Timestamps" hint={HINT}><Toggle on onChange={() => {}} label="Timestamps" /></FormsField>,
    )
    expect(describedText(container.querySelector('[role="switch"]'), container)).toBe(HINT)
  })

  it('a read-only display switch is described as well', () => {
    const { container } = render(
      <Row label="Timestamps" hint={HINT}><Toggle on readOnly label="Timestamps" /></Row>,
    )
    const sw = container.querySelector('[role="switch"]')
    expect(sw!.tagName.toLowerCase()).toBe('span')
    expect(describedText(sw, container)).toBe(HINT)
  })

  it('a DECORATIVE switch stays out of the a11y tree entirely', () => {
    const { container } = render(
      <Row label="Timestamps" hint={HINT}><Toggle on readOnly decorative label="Timestamps" /></Row>,
    )
    expect(container.querySelector('[role="switch"]')).toBeNull()
    expect(container.querySelector('[aria-hidden]')!.getAttribute('aria-describedby')).toBeNull()
  })

  it('NO hint means NO aria-describedby — never a dangling pointer', () => {
    const { container } = render(
      <Row label="Timestamps"><Toggle on={false} onChange={() => {}} label="Timestamps" /></Row>,
    )
    const sw = container.querySelector('[role="switch"]')
    expect(sw!.getAttribute('aria-describedby')).toBeNull()
    expect(describedText(sw, container)).toBe('')
  })

  it('a natively disabled switch (no reason) still gets the hint', () => {
    const { container } = render(
      <Row label="Timestamps" hint={HINT}><Toggle on={false} onChange={() => {}} disabled label="Timestamps" /></Row>,
    )
    const sw = container.querySelector('[role="switch"]')
    expect(sw!.hasAttribute('disabled')).toBe(true)
    expect(describedText(sw, container)).toBe(HINT)
  })
})

describe('soft-off precedence: the REASON survives, because describedby outranks title', () => {
  it('a soft-off switch announces its reason, NOT the row hint', () => {
    const { container } = render(
      <Row label="Offer password sign-in" hint="Set a password first. Turning this on without one would show a form nobody can pass.">
        <Toggle on={false} onChange={() => {}} disabled disabledReason={REASON} label="Offer password sign-in" />
      </Row>,
    )
    const sw = container.querySelector('[role="switch"]')!
    expect(sw.getAttribute('aria-disabled')).toBe('true')
    expect(sw.getAttribute('aria-describedby')).toBeNull()
    expect(describedText(sw, container)).toBe(REASON)
  })

  it('clearing the precondition hands the hint back on the same render', () => {
    const { container } = render(
      <Row label="Offer password sign-in" hint={HINT}>
        <Toggle on={false} onChange={() => {}} label="Offer password sign-in" />
      </Row>,
    )
    expect(describedText(container.querySelector('[role="switch"]'), container)).toBe(HINT)
  })
})

describe('the other two live non-consumers in the family', () => {
  it('a Combobox trigger in a hinted Row is described', () => {
    const { container } = render(
      <Row label="Default agent" hint="Used for every new session.">
        <Combobox options={[{ value: 'a', label: 'Agent A' }]} value="a" onChange={() => {}} />
      </Row>,
    )
    const trigger = container.querySelector('button[aria-haspopup="listbox"]')
    expect(trigger).not.toBeNull()
    expect(describedText(trigger, container)).toBe('Used for every new session.')
  })

  it('a ShortcutRecorder in a hinted Field is described', () => {
    const hint = 'Used by the desktop app for global push-to-talk.'
    const { container } = render(
      <FormsField label="Push-to-talk shortcut" hint={hint}>
        <ShortcutRecorder label="Push-to-talk shortcut" value="Cmd+Shift+Space"
          format={(c) => c} parse={() => ''} onRecord={() => {}} />
      </FormsField>,
    )
    const btn = container.querySelector('button')
    expect(describedText(btn, container)).toBe(hint)
    expect(btn!.getAttribute('aria-label')).toContain('activate to change')
  })
})


const UI_DIR = import.meta.dirname
const tsxFiles = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return tsxFiles(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

const EXPECTED_CONSUMERS = [
  'ChipInput', 'Combobox', 'DateInput', 'NumberField', 'Select',
  'ShortcutRecorder', 'TextArea', 'TextInput', 'Toggle',
].sort()

function exportSpans(rawSrc: string): Map<string, string> {
  const src = rawSrc
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/\/\/[^\n]*/g, (m) => ' '.repeat(m.length))
  const re = /^export (?:function|const) ([A-Za-z0-9_]+)/gm
  const hits: { name: string; at: number }[] = []
  for (let m = re.exec(src); m; m = re.exec(src)) hits.push({ name: m[1], at: m.index })
  const out = new Map<string, string>()
  hits.forEach((h, i) => out.set(h.name, src.slice(h.at, i + 1 < hits.length ? hits[i + 1].at : src.length)))
  return out
}

describe('the hint-consumer census', () => {
  it('every expected primitive reads the hint id AND emits aria-describedby IN ITS OWN BODY', () => {
    const files = tsxFiles(UI_DIR)
    expect(files.length).toBeGreaterThanOrEqual(40)

    const spans = new Map<string, { file: string; body: string }>()
    for (const f of files) {
      for (const [name, body] of exportSpans(readFileSync(f, 'utf8'))) {
        if (EXPECTED_CONSUMERS.includes(name)) spans.set(name, { file: f, body })
      }
    }
    expect([...spans.keys()].sort()).toEqual(EXPECTED_CONSUMERS)

    for (const [name, { file, body }] of spans) {
      expect(body.length, `${name}'s span in ${file} is implausibly short — span-splitting broke`).toBeGreaterThan(120)
      expect(body, `${name} (${file}) does not read the published hint id`).toMatch(/useFieldHintId\(\)/)
      expect(body, `${name} (${file}) reads the hint id but never emits aria-describedby`).toMatch(/aria-describedby=/)
    }
  })

  it('Toggle wires the hint through a softOff-aware binding, not unconditionally', () => {
    const src = readFileSync(join(UI_DIR, 'Toggle.tsx'), 'utf8')
    expect(src).toMatch(/state\.ariaDisabled\s*\?\s*undefined\s*:\s*hint/)
  })
})

describe('a layout that publishes a label id publishes the hint id too', () => {
  const SRC_DIR = join(UI_DIR, '../..')
  function publishers(): string[] {
    const out: string[] = []
    for (const abs of tsxFiles(SRC_DIR)) {
      if (abs.endsWith('shared/ui/forms.tsx')) continue
      const src = readFileSync(abs, 'utf8')
      if (/<FieldLabelProvider\b/.test(src)) out.push(abs.slice(SRC_DIR.length + 1))
    }
    return out
  }

  const found = publishers()

  it('found the local layouts (vacuity floor)', () => {
    expect(found.length, `publishers found: ${found.join(', ')}`).toBeGreaterThanOrEqual(2)
    expect(found).toContain('features/projects/ProjectsSection.tsx')
    expect(found).toContain('features/settings/settingsUI.tsx')
  })

  it('none publishes only half the contract', () => {
    const halfDone = found.filter((rel) => !/<FieldHintProvider\b/.test(readFileSync(join(SRC_DIR, rel), 'utf8')))
    expect(
      halfDone,
      `these publish a label id but no hint id, so a control inside them resolves a name and no ` +
        `description:\n  ${halfDone.join('\n  ')}`,
    ).toEqual([])
  })

  it("the projects modal's hint carries the id it publishes", () => {
    const src = readFileSync(join(SRC_DIR, 'features/projects/ProjectsSection.tsx'), 'utf8')
    expect(src, 'the hint span must carry the published id').toMatch(/\{hint && <span id=\{hintId\}/)
    expect(src, 'the id is conditional on there being a hint').toMatch(/<FieldHintProvider value=\{hint \? hintId : undefined\}>/)
  })
})
