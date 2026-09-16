import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const chatPanel = () => readFileSync(join(SRC, 'features/settings/ChatPanel.tsx'), 'utf8')

function rowBodies(src: string): string[] {
  return [...src.matchAll(/<Row\b[\s\S]*?<\/Row>/g)].map((m) => m[0])
}

describe('a settings Row labels exactly one control', () => {
  it('reads the real file (not vacuously green)', () => {
    const rows = rowBodies(chatPanel())
    expect(rows.length, 'ChatPanel must have Rows to check').toBeGreaterThan(5)
  })

  it('no Row in ChatPanel holds two Toggles', () => {
    const offenders = rowBodies(chatPanel())
      .filter((r) => (r.match(/<Toggle\b/g) || []).length > 1)
      .map((r) => (r.match(/label="([^"]+)"/) || [])[1] ?? '(unlabelled)')
    expect(offenders, `these Rows share one caption across two switches: ${offenders.join(', ')}`).toEqual([])
  })

  it('the check-work toggle has its own Row, label and hint', () => {
    const src = chatPanel()
    const row = rowBodies(src).find((r) => /offer_check_work/.test(r)) ?? ''
    expect(row, 'the check-work toggle must live in a Row').toContain('<Row')
    expect(row, 'with a visible label naming it').toMatch(/label="Offer “Check this work”"/)
    expect(row, 'and a hint about verification, not suggestions').toMatch(/hint="[^"]*re-runs the checks[^"]*"/)
    expect((row.match(/<Toggle\b/g) || []).length, 'exactly one control').toBe(1)
  })

  it("the follow-up Row keeps its own hint and only its own switch", () => {
    const row = rowBodies(chatPanel()).find((r) => /followup_chips/.test(r)) ?? ''
    expect(row).toMatch(/label="Follow-up suggestions"/)
    expect(row).toMatch(/suggested next messages/)
    expect(/offer_check_work/.test(row), 'the two features must not share a row').toBe(false)
  })

  it('Row still renders one label and puts children on the right — the reason this matters', () => {
    const ui = readFileSync(join(SRC, 'features/settings/settingsUI.tsx'), 'utf8')
    const start = ui.indexOf('export function Row(')
    expect(start, 'Row must exist, and must not be shadowed by a prefix sibling').toBeGreaterThan(-1)
    const next = ui.indexOf('\nexport ', start + 1)
    const row = ui.slice(start, next > start ? next : undefined)
    expect(row).toMatch(/\{label\}/)
    expect(row).toMatch(/col-start-2 row-start-1 flex items-center">\{children\}/)
  })
})
