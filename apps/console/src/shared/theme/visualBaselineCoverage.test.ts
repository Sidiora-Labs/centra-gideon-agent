import { describe, expect, it } from 'vitest'
import { readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { ROUTES, VIEW_ROUTES, THEMES } from '../../../e2e/routes'


const BASELINES = join(process.cwd(), "e2e/__screenshots__/visual.spec.ts")

function expectedKeys(): string[] {
  const keys: string[] = []
  for (const theme of THEMES) {
    for (const { route, id } of [...ROUTES, ...VIEW_ROUTES]) {
      keys.push(`${id ?? route}-${theme}`)
    }
  }
  return keys
}

function byPlatform(): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>()
  for (const name of readdirSync(BASELINES)) {
    const m = /^(.+)-([a-z0-9]+)\.png$/.exec(name)
    if (!m) continue
    const [, key, platform] = m
    if (!out.has(platform)) out.set(platform, new Set())
    out.get(platform)!.add(key)
  }
  return out
}

const UNCAPTURED: { key: string; why: string }[] = [
  { key: 'artifacts-light', why: 'route added to routes.ts without capturing goldens' },
  { key: 'artifacts-dark', why: 'route added to routes.ts without capturing goldens' },
  { key: 'learning-light', why: 'route added to routes.ts without capturing goldens' },
  { key: 'learning-dark', why: 'route added to routes.ts without capturing goldens' },
  { key: 'knowledge-graph-light', why: 'VIEW_ROUTE added without capturing goldens' },
  { key: 'knowledge-graph-dark', why: 'VIEW_ROUTE added without capturing goldens' },
]

describe('the visual gate has a committed baseline for every surface it snapshots', () => {
  it('the baseline directory and the route manifest are both non-empty (vacuity floor)', () => {
    expect(existsSync(BASELINES), `the baseline directory is gone: ${BASELINES}`).toBe(true)
    expect(readdirSync(BASELINES).length, 'no committed goldens at all').toBeGreaterThan(10)
    expect(expectedKeys().length, 'the route manifest yielded no surfaces').toBeGreaterThan(10)
  })

  it('every platform that has ANY baseline has a COMPLETE set', () => {
    const platforms = byPlatform()
    expect(platforms.size, 'no platform-suffixed goldens found — has the naming changed?').toBeGreaterThan(0)

    const allowed = new Set(UNCAPTURED.map((u) => u.key))
    const problems: string[] = []
    for (const [platform, have] of platforms) {
      const missing = expectedKeys().filter((k) => !have.has(k) && !allowed.has(k))
      if (missing.length) {
        problems.push(`${platform}: ${missing.length} missing → ${missing.join(', ')}`)
      }
    }
    expect(
      problems,
      `a surface in routes.ts has no committed golden for a platform that has others. ` +
        `e2e/visual.spec.ts does not skip it — playwright writes the actual image and FAILS, so a ` +
        `missing file is indistinguishable from a real regression in that report. Capture with ` +
        `\`npm run e2e:update\` and commit the goldens in the same change as the route:\n  ` +
        problems.join('\n  '),
    ).toEqual([])
  })

  it('every recorded UNCAPTURED surface is still uncaptured', () => {
    const have = new Set<string>()
    for (const set of byPlatform().values()) for (const k of set) have.add(k)
    for (const { key, why } of UNCAPTURED) {
      expect(
        have.has(key),
        `UNCAPTURED names "${key}", but a baseline now exists for it. The gate is wider than the ` +
          `repo needs: DELETE the entry.\n  ${why}`,
      ).toBe(false)
    }
  })

  it('no golden is orphaned — every committed baseline maps to a surface still in the manifest', () => {
    const expected = new Set(expectedKeys())
    const orphans: string[] = []
    for (const [platform, have] of byPlatform()) {
      for (const key of have) if (!expected.has(key)) orphans.push(`${key}-${platform}.png`)
    }
    expect(
      orphans,
      `these committed goldens correspond to no surface in routes.ts — the route was removed or ` +
        `renamed and its baseline was left behind:\n  ${orphans.join('\n  ')}`,
    ).toEqual([])
  })
})
