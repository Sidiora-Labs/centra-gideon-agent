import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'


const PAGES_ROOT = join(process.cwd(), "src/features")

function listTsx(dir: string): string[] {
  const out: string[] = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) out.push(...listTsx(p))
    else if (e.name.endsWith('.tsx')) out.push(p)
  }
  return out
}

const HAND_ROLLED = /className="[^"]*(?:text-primary(?:-emphasis)?[^"]*hover:underline|hover:underline[^"]*text-primary(?:-emphasis)?)[^"]*"/

describe('TextLink adoption rail (no hand-rolled inline text links)', () => {
  it('no page hand-rolls the text-link idiom', () => {
    const offenders = listTsx(PAGES_ROOT)
      .filter((p) => HAND_ROLLED.test(readFileSync(p, 'utf8')))
      .map((p) => p.slice(PAGES_ROOT.length + 1))
    expect(
      offenders,
      `Hand-rolled text link(s) in: ${offenders.join(', ')}. Use the TextLink primitive ` +
        `(ui/TextLink.tsx) — it carries the AA-audited ink pair, the hover treatment, and ` +
        `the SC 2.5.8 hit-area padding in one place. Recolor via the ink prop, never className.`,
    ).toEqual([])
  })

  it('the scan is still looking (vacuity floor)', () => {
    expect(HAND_ROLLED.test('className="text-primary hover:underline"')).toBe(true)
    expect(listTsx(PAGES_ROOT).length).toBeGreaterThan(50)
  })
})
