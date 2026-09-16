import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'


const SRC = join(process.cwd(), "src")

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.tsx?$/.test(p) && !/\.test\.tsx?$/.test(p)) out.push(p)
  }
  return out
}

const FILES = walk(SRC)
const rel = (f: string) => relative(SRC, f).replace(/\\/g, '/')
const read = (f: string) => readFileSync(f, 'utf8')

const BFS_SIGNATURES: [label: string, pattern: RegExp][] = [
  ['a hop-indexed expansion loop', /for\s*\(\s*let\s+hop\s*=\s*0\s*;/],
  ['a mutable BFS frontier', /\blet\s+frontier\b/],
  ['the get-or-create adjacency build', /adj\.get\([^)]*\)\s*\?\?\s*adj\.set\(/],
]

const THE_MODULE = 'shared/data/litSet.ts'
const CALLERS = ['features/settings/MemoryGraph.tsx', 'features/knowledge/KnowledgeEgoGraph.tsx']

describe('the lit-set BFS has exactly one implementation', () => {
  it('found the source tree at all (vacuity floor for every assertion below)', () => {
    expect(FILES.length).toBeGreaterThan(300)
  })

  it('every signature still matches real code (vacuity floor per pattern)', () => {
    for (const [label, pattern] of BFS_SIGNATURES) {
      const hits = FILES.filter((f) => pattern.test(read(f))).map(rel)
      expect(hits, `${label} matched nothing — the pattern rotted, so this rail proves nothing`)
        .not.toHaveLength(0)
    }
  })

  it('and exactly one file defines it', () => {
    const definers = FILES.filter((f) => {
      const src = read(f)
      return BFS_SIGNATURES.some(([, pattern]) => pattern.test(src))
    }).map(rel)
    expect(definers).toEqual([THE_MODULE])
  })

  it('both graph canvases import it instead of carrying their own', () => {
    for (const caller of CALLERS) {
      const src = read(join(SRC, caller))
      expect(src, `${caller} must call the shared traversal`).toMatch(/litNeighbourhood/)
      expect(src, `${caller} must import it from lib/litSet`).toMatch(/from '\.\.\/\.\.\/shared\/data\/litSet'/)
      for (const [label, pattern] of BFS_SIGNATURES) {
        expect(pattern.test(src), `${caller} must not re-declare ${label}`).toBe(false)
      }
    }
  })
})
