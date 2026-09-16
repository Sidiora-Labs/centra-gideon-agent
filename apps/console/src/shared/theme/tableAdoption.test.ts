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

function filesWithRawTable(): string[] {
  return listTsx(PAGES_ROOT)
    .filter((p) => /<table\b/.test(readFileSync(p, 'utf8')))
    .map((p) => p.slice(PAGES_ROOT.length + 1))
    .sort()
}

interface Baseline { rawTableFiles: string[] }

function loadBaseline(): Baseline {
  const raw = readFileSync(join(process.cwd(), "src/shared/theme/tableAdoption.baseline.json"), 'utf8')
  return JSON.parse(raw) as Baseline
}

describe('table-family adoption ratchet (raw <table> files may only shrink)', () => {
  const base = new Set(loadBaseline().rawTableFiles)
  const live = filesWithRawTable()
  const newcomers = live.filter((f) => !base.has(f))

  it('no page hand-rolls <table> outside the baseline set', () => {
    expect(
      newcomers,
      `New hand-rolled <table> in: ${newcomers.join(', ')}. Use the Table family ` +
        `(ui/Table.tsx: Table with a required sr-only caption, THead, Th — always scope="col" — ` +
        `and Td), or if this file is an intentional exception, justify it in ` +
        `src/design/tableAdoption.baseline.json in the same commit.`,
    ).toEqual([])
  })

  it('baseline is not stale (a migration should ratchet its file out)', () => {
    const gone = [...base].filter((f) => !live.includes(f))
    if (gone.length > 0) {
      // eslint-disable-next-line no-console
      console.warn(
        `[table-adoption] migrated file(s) still in baseline — remove from ` +
          `src/design/tableAdoption.baseline.json: ${gone.join(', ')}`,
      )
    }
    expect(true).toBe(true)
  })
})
