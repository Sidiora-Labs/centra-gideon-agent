import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const FILE = join(process.cwd(), "src/features/tasks/TaskDetail.tsx")
const src = readFileSync(FILE, 'utf8')

function buttonTags(): string[] {
  const tags: string[] = []
  let i = 0
  while ((i = src.indexOf('<button', i)) !== -1) {
    let depth = 0
    for (let k = i + 7; k < src.length; k++) {
      const c = src[k]
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) { tags.push(src.slice(i, k + 1)); i = k + 1; break }
      if (k === src.length - 1) i = src.length
    }
  }
  return tags
}

const TICKS = ['Mark criterion', 'Mark step']

describe("the task panel's tick targets clear the 24px floor", () => {
  const tags = buttonTags()

  it('the scan found the panel\'s buttons (vacuity floor)', () => {
    expect(tags.length, 'no <button> tags parsed out of TaskDetail.tsx').toBeGreaterThanOrEqual(5)
    for (const label of TICKS) {
      expect(tags.some((t) => t.includes(label)), `no button matched ${label}`).toBe(true)
    }
  })

  it('each tick button IS the 24px target', () => {
    for (const label of TICKS) {
      const tag = tags.find((t) => t.includes(label))!
      expect(tag, `${label}: the target box must be size-6 (24px)`).toMatch(/\bsize-6\b/)
      expect(tag, `${label}: size-4 was the 16px defect`).not.toMatch(/\bsize-4\b/)
      expect(tag, `${label}: size-5 was the 20px defect`).not.toMatch(/\bsize-5\b/)
    }
  })

  it('the reclaim is HORIZONTAL only — never -my-*, which would overlap stacked targets', () => {
    for (const label of TICKS) {
      const tag = tags.find((t) => t.includes(label))!
      expect(tag, `${label}: expected a horizontal reclaim`).toMatch(/-mx-(?:0\.5|1)\b/)
      expect(
        tag,
        `${label}: a vertical reclaim would make consecutive 24px targets overlap — the list must ` +
          `spend the height instead`,
      ).not.toMatch(/-my-|-mt-|-mb-/)
    }
  })

  it('the PAINT stays its own size, on an inner span', () => {
    const criterion = src.slice(src.indexOf('Mark criterion'))
    expect(criterion.slice(0, 700), 'the criterion tick keeps a 16px painted square')
      .toMatch(/<span className="inline-flex size-4 [^"]*rounded-sm/)
    const step = src.slice(src.indexOf('Mark step'))
    expect(step.slice(0, 900), 'the step marker keeps a 20px painted pill')
      .toMatch(/<span className="inline-flex size-5 [^"]*rounded-pill/)
  })

  it('the hover ring moved to the paint and still respects disabled', () => {
    for (const label of TICKS) {
      const after = src.slice(src.indexOf(label), src.indexOf(label) + 900)
      expect(after, `${label}: the ring belongs on the painted glyph`).toMatch(/group-hover:ring-2/)
      expect(after, `${label}: read-only must stay ringless`).toMatch(/group-disabled:ring-0/)
      expect(after, `${label}: the button needs the group anchor`).toMatch(/className="group /)
    }
  })
})
