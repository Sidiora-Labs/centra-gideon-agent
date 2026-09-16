import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { ChipInput, Field } from './forms'


const SRC = join(process.cwd(), "src")
const forms = readFileSync(join(SRC, 'shared/ui/forms.tsx'), 'utf8')

function chipInputTag(): string {
  const start = forms.indexOf('export function ChipInput')
  expect(start, 'ChipInput was renamed or removed').toBeGreaterThan(0)
  const body = forms.slice(start, forms.indexOf('\nexport ', start + 10))
  const i = body.indexOf('<input ')
  expect(i, "ChipInput no longer renders an <input>").toBeGreaterThan(0)
  let depth = 0
  for (let k = i + 7; k < body.length; k++) {
    const c = body[k]
    if (c === '{') depth++
    else if (c === '}') depth--
    else if (c === '>' && depth === 0) return body.slice(i, k + 1)
  }
  throw new Error('could not find the end of the ChipInput <input> tag')
}

describe("the chip field clears SC 2.5.8's 24px floor", () => {
  const tag = chipInputTag()

  it('the field carries the 24px hit-box floor', () => {
    expect(tag, "ChipInput's field lost its 24px minimum hit box").toMatch(/\bmin-h-6\b/)
  })

  it('it uses the app\'s established idiom, not a bespoke height', () => {
    expect(tag).toMatch(/\bflex-1\b/)
    expect(tag, 'a fixed h-* would stop the field tracking its row').not.toMatch(/(?<!min-)\bh-\d/)
  })

  it('is pixel-neutral by construction — the well already reserves exactly 24px', () => {
    const start = forms.indexOf('export function ChipInput')
    const well = forms.slice(start, forms.indexOf('<input ', start))
    expect(well).toMatch(/\bmin-h-10\b/)
    expect(well).toMatch(/\bpy-2\b/)
  })

  it('the well routes a click into the field, and only its own background', () => {
    const start = forms.indexOf('export function ChipInput')
    const well = forms.slice(start, forms.indexOf('<input ', start))
    expect(well).toMatch(/onMouseDown=/)
    expect(well).toMatch(/event\.target === event\.currentTarget/)
    expect(well).toMatch(/focus\(\)/)
    expect(well).toMatch(/preventDefault\(\)/)
  })

  it('still renders, names itself, and keeps its chips (no behaviour lost)', () => {
    render(<ChipInput values={['alpha', 'beta']} onChange={() => {}} placeholder="Add a tag, Enter" />)
    expect(screen.getByRole('textbox', { name: 'Add a tag' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove alpha' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove beta' })).toBeTruthy()
  })

  it('inside a Field it still takes the Field\'s label, not the fallback', () => {
    render(<Field label="Tags"><ChipInput values={[]} onChange={() => {}} /></Field>)
    expect(screen.getByRole('textbox', { name: 'Tags' })).toBeTruthy()
  })
})

describe('the chip field is shared widely enough to be worth a primitive fix', () => {
  function walk(dir: string, out: string[] = []): string[] {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const abs = join(dir, e.name)
      if (e.isDirectory()) walk(abs, out)
      else if (/\.tsx?$/.test(e.name) && !e.name.includes('.test.')) out.push(abs)
    }
    return out
  }

  it('has at least 10 production call sites (vacuity floor)', () => {
    const sites: string[] = []
    for (const abs of walk(SRC)) {
      if (abs.endsWith('shared/ui/forms.tsx')) continue
      const src = readFileSync(abs, 'utf8')
      for (const _ of src.matchAll(/<ChipInput[\s/>]/g)) sites.push(abs.slice(SRC.length + 1))
    }
    expect(sites.length, `ChipInput call sites found: ${sites.join(', ')}`).toBeGreaterThanOrEqual(10)
    const areas = new Set(sites.map((s) => s.split('/').slice(0, 2).join('/')))
    expect(areas.size).toBeGreaterThanOrEqual(4)
  })
})

describe("the chip's remove button clears SC 2.5.8's 24px floor", () => {
  const chipStart = forms.indexOf('export function ChipInput')
  const body = forms.slice(chipStart, forms.indexOf('\nexport ', chipStart + 10))

  it('found the chip and its remove button (vacuity floor)', () => {
    expect(chipStart, 'ChipInput was renamed or removed').toBeGreaterThan(0)
    expect(body, 'the remove button is gone').toMatch(/aria-label=\{`Remove \$\{value\}`\}/)
  })

  it('the remove button IS the 24px target', () => {
    const i = body.indexOf('aria-label={`Remove')
    const tag = body.slice(body.lastIndexOf('<button', i), body.indexOf('</button>', i))
    expect(tag, 'the remove button must be a 24px box').toMatch(/\bsize-6\b/)
    expect(tag, 'it must centre its glyph, or the 24px box is not a target the glyph sits in')
      .toMatch(/items-center/)
    expect(tag, 'the glyph stays 12px — the box grew, the icon did not').toMatch(/<X size=\{12\}/)
  })

  it('the chip spent its trailing space, so its width is unchanged', () => {
    const chipOpen = body.indexOf('<span key={value}')
    const chipTag = body.slice(chipOpen, body.indexOf('\n', chipOpen))
    expect(chipTag, 'the left padding is untouched').toMatch(/\bpl-2\b/)
    expect(chipTag, 'the right padding was spent on the button').toMatch(/\bpr-0\b/)
    expect(chipTag, 'px-2 would re-add the 8px the button now occupies').not.toMatch(/\bpx-2\b/)
    expect(chipTag, 'gap-1 would re-add the 4px the button now occupies').not.toMatch(/\bgap-1\b/)
  })
})
