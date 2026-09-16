import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const HERE = join(process.cwd(), "src/features/dashboard")

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) { out.push(...walk(abs)); continue }
    if (!name.endsWith('.tsx') || name.includes('.test.')) continue
    out.push(abs)
  }
  return out
}

type Slot = { file: string; text: string; hasAction: boolean; usesSlotAction: boolean }

function openTagEnd(slice: string): number {
  let depth = 0
  for (let i = '<SlotEmptyState'.length; i < slice.length; i++) {
    const c = slice[i]
    if (c === '{') depth++
    else if (c === '}') depth--
    else if (c === '>' && depth === 0) return i + 1
  }
  return -1
}

function slots(): Slot[] {
  const found: Slot[] = []
  for (const abs of walk(HERE)) {
    const src = readFileSync(abs, 'utf8')
    const file = abs.slice(abs.lastIndexOf('/') + 1)
    for (const m of src.matchAll(/<SlotEmptyState[\s>][\s\S]*?<\/SlotEmptyState>/g)) {
      const slice = m[0]
      const end = openTagEnd(slice)
      expect(end, `could not find the end of the opening tag in ${file}`).toBeGreaterThan(0)
      const open = slice.slice(0, end)
      const text = slice
        .slice(end)
        .replace(/<\/SlotEmptyState>/, '')
        .replace(/\{[^{}]*\}/g, ' ')
        .replace(/<[^>]*>/g, ' ')
        .replace(/&rsquo;|&#39;/g, "'").replace(/&mdash;/g, '—').replace(/&amp;/g, '&')
        .replace(/\s+/g, ' ').trim()
      found.push({
        file,
        text,
        hasAction: /\baction=/.test(open),
        usesSlotAction: /<SlotAction[\s>]/.test(open),
      })
    }
  }
  return found
}

const isReadError = (t: string) => /^(Couldn't|Could not)\b/i.test(t) || /read error/i.test(t)

const clauses = (t: string) => t.split(/[.—]/).map((s) => s.trim()).filter(Boolean)
const teaches = (s: Slot) => s.hasAction || clauses(s.text).length >= 2

describe('openTagEnd — the parse the tree cannot exercise on its own', () => {
  const body = (s: string) => s.slice(openTagEnd(s)).replace(/<\/SlotEmptyState>/, '').trim()

  it('stops at the tag-closing > even when the attributes contain > and =>', () => {
    const s = `<SlotEmptyState icon={X} action={<SlotAction icon={Plus} onClick={() => go('a')}>New thing</SlotAction>}>No things.</SlotEmptyState>`
    expect(body(s)).toBe('No things.')
    expect(clauses(body(s))).toHaveLength(1)
  })

  it('a period inside the action label cannot manufacture a second clause', () => {
    const s = `<SlotEmptyState icon={X} action={<SlotAction icon={Plus} onClick={go}>Add. Now</SlotAction>}>No things.</SlotEmptyState>`
    expect(clauses(body(s))).toHaveLength(1)
  })

  it('the plain form is unaffected', () => {
    const s = `<SlotEmptyState icon={X}>No things. They appear here as they run.</SlotEmptyState>`
    expect(clauses(body(s))).toHaveLength(2)
  })
})

describe('dashboard slot-empty states teach a mechanism or offer a step', () => {
  const all = slots()
  const errors = all.filter((s) => isReadError(s.text))
  const empties = all.filter((s) => !isReadError(s.text))

  it('the scan actually found the family (vacuity floor)', () => {
    expect(all.length).toBeGreaterThanOrEqual(13)
    expect(empties.length).toBeGreaterThanOrEqual(10)
    expect(errors.length).toBeGreaterThanOrEqual(3)
    expect(all.every((s) => s.text.length > 0)).toBe(true)
  })

  it('no empty state is a bare fact', () => {
    const failing = empties.filter((s) => !teaches(s))
    expect(
      failing.map((s) => `${s.file}: "${s.text}"`),
      'a dashboard slot-empty state states a bare fact with no mechanism and no on-ramp',
    ).toHaveLength(0)
  })

  it('the Schedule slot names its mechanism AND offers the on-ramp', () => {
    const s = slots().find((x) => x.file === 'ScheduleWidget.tsx' && /No recent scheduled runs/.test(x.text))
    expect(s, 'ScheduleWidget no longer has a "no recent scheduled runs" empty state').toBeTruthy()
    expect(s!.text).toMatch(/appear here as they fire/)
    expect(clauses(s!.text).length).toBeGreaterThanOrEqual(2)
    expect(s!.hasAction).toBe(true)
    expect(s!.usesSlotAction).toBe(true)
  })

  it('the slot on-ramp has exactly one definition — no second copy', () => {
    const withAction = slots().filter((s) => s.hasAction)
    expect(withAction.length).toBeGreaterThanOrEqual(3)
    for (const s of withAction) {
      expect(s.usesSlotAction, `${s.file} does not route its slot on-ramp through SlotAction`).toBe(true)
    }
    const kit = readFileSync(join(HERE, 'widgets/kit.tsx'), 'utf8')
    expect(kit.match(/export function SlotAction\b/g) ?? []).toHaveLength(1)
  })
})
