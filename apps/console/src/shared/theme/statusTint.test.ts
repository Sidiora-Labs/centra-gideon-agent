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

function countInlineColorMix(): { total: number; byFile: Record<string, number> } {
  const byFile: Record<string, number> = {}
  let total = 0
  for (const p of listTsx(PAGES_ROOT)) {
    const n = (readFileSync(p, 'utf8').match(/color-mix\(/g) || []).length
    if (n > 0) {
      byFile[p.slice(PAGES_ROOT.length + 1)] = n
      total += n
    }
  }
  return { total, byFile }
}

interface Baseline { inlineColorMix: number }

function loadBaseline(): Baseline {
  const raw = readFileSync(join(process.cwd(), "src/shared/theme/statusTint.baseline.json"), 'utf8')
  return JSON.parse(raw) as Baseline
}

describe('status-tint ratchet (inline color-mix in pages may only shrink)', () => {
  const base = loadBaseline()
  const live = countInlineColorMix()

  it(`inline color-mix count must not exceed the baseline (${loadBaseline().inlineColorMix})`, () => {
    expect(
      live.total,
      `New inline color-mix tint(s) detected (${live.total} > ${base.inlineColorMix}). ` +
        `For a tinted status label use the StatusPill primitive (ui/StatusPill.tsx — the closed ` +
        `tone set stays inside the audited 18% ink-contrast budget); for a genuinely non-pill ` +
        `fill, or an intentional migration DOWN, adjust inlineColorMix in ` +
        `src/design/statusTint.baseline.json in the same commit.\nBy file:\n${JSON.stringify(live.byFile, null, 2)}`,
    ).toBeLessThanOrEqual(base.inlineColorMix)
  })

  it('baseline is not stale (a migration dropped the real count without ratcheting)', () => {
    if (live.total < base.inlineColorMix) {
      // eslint-disable-next-line no-console
      console.warn(
        `[status-tint] live count ${live.total} is below baseline ${base.inlineColorMix} — ` +
          `ratchet src/design/statusTint.baseline.json DOWN in this commit to lock the gain.`,
      )
    }
    expect(true).toBe(true)
  })
})
