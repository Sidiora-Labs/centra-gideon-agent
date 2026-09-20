import { describe, expect, it } from 'vitest'
import { readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { ROUTES, VIEW_ROUTES, THEMES } from '../../../e2e/routes'
import { VISUAL_BASELINE_PLATFORMS } from '../../../playwright.config'


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

describe('the visual gate has a committed baseline for every surface it snapshots', () => {
  it('the baseline directory and the route manifest are both non-empty (vacuity floor)', () => {
    expect(existsSync(BASELINES), `the baseline directory is gone: ${BASELINES}`).toBe(true)
    expect(readdirSync(BASELINES).length, 'no committed goldens at all').toBeGreaterThan(10)
    expect(expectedKeys().length, 'the route manifest yielded no surfaces').toBeGreaterThan(10)
  })

  it('every declared platform has a complete set', () => {
    const platforms = byPlatform()
    const problems: string[] = []
    for (const platform of VISUAL_BASELINE_PLATFORMS) {
      const have = platforms.get(platform) ?? new Set()
      const missing = expectedKeys().filter((key) => !have.has(key))
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

  it('contains baselines only for declared platforms', () => {
    expect([...byPlatform().keys()].sort()).toEqual([...VISUAL_BASELINE_PLATFORMS].sort())
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
