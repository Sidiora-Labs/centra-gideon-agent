import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { scanInertUtilities, loadUtilityOracle } from './consistencyAudit.report'


interface Allowlist { allow: Record<string, string[]> }

function tokenFamilyFor(utility: string): string {
  const base = utility.replace(/^(?:[\w@/[\]().-]+:)+/, '').replace(/^!?-?/, '')
  if (base.startsWith('rounded-')) return '--radius-*'
  if (base.startsWith('gap-')) return '--spacing-*'
  return '--color-*'
}

function loadAllowlist(): Allowlist {
  const raw = readFileSync(join(process.cwd(), "src/shared/theme/inertUtilities.allowlist.json"), 'utf8')
  const j = JSON.parse(raw) as Allowlist
  return { allow: j.allow ?? {} }
}

describe('inert-utility rail (a text-/bg-/border-/rounded-/gap- class must emit CSS)', () => {
  it('the oracle agrees with the design tokens (it can tell live from inert)', async () => {
    const isLive = await loadUtilityOracle()
    for (const live of ['text-on-surface-low', 'border-outline-variant', 'text-primary', 'bg-surface']) {
      expect(isLive(live), `${live} is a real token and must compile`).toBe(true)
    }
    for (const dead of ['text-muted', 'border-border', 'bg-accent-subtle', 'text-accent']) {
      expect(isLive(dead), `${dead} has no token and must NOT compile`).toBe(false)
    }
    for (const live of [
      'text-center', 'border-t', 'bg-transparent', 'bg-white', 'text-sm',
      'bg-gradient-to-br', 'text-on-surface-low/40', 'hover:bg-primary/15',
      'group-hover/dock:text-on-surface', 'text-[0.75rem]', 'border-l-[3px]',
    ]) {
      expect(isLive(live), `${live} must not be reported as inert`).toBe(true)
    }
    for (const live of [
      'rounded-md', 'rounded-lg', 'rounded-pill', 'rounded-squircle', 'rounded-l-md',
      'rounded-none', 'gap-xs', 'gap-s', 'gap-m', 'gap-2xl', 'gap-1', 'gap-x-2',
      'sm:gap-xs',
    ]) {
      expect(isLive(live), `${live} must not be reported as inert`).toBe(true)
    }
    for (const dead of ['rounded-m', 'gap-2xs', 'sm:gap-2xs', 'rounded-mdd', 'gap-nope']) {
      expect(isLive(dead), `${dead} has no token and must NOT compile`).toBe(false)
    }
  })

  it('no NEW inert utility outside the shrinking allowlist', async () => {
    const { allow } = loadAllowlist()
    const offenders: Record<string, string[]> = {}
    for (const hit of await scanInertUtilities()) {
      if (allow[hit.file]?.includes(hit.base)) continue
      offenders[hit.file] ??= []
      offenders[hit.file].push(`${hit.line}: ${hit.utility} — no ${tokenFamilyFor(hit.utility)} token; emits NO CSS`)
    }
    expect(
      offenders,
      'These utilities compile to nothing, so their styling is silently absent. ' +
        'Use a token that exists (see design/tokens.css), or add the token if it is ' +
        'genuinely new. Note the scales do NOT share spellings: spacing is ' +
        'xs/s/m/l/xl/2xs/2xl/3xl while radius is xs/sm/md/lg/lgi/xl/xli/2xl — so ' +
        '`rounded-m` is dead even though `gap-m` is live:\n' +
        JSON.stringify(offenders, null, 2),
    ).toEqual({})
  })

  it('allowlist has no stale entries (a fixed utility must leave it)', async () => {
    const { allow } = loadAllowlist()
    const live = await scanInertUtilities()
    const stale: string[] = []
    for (const [file, utilities] of Object.entries(allow)) {
      for (const base of utilities) {
        if (!live.some((h) => h.file === file && h.base === base)) stale.push(`${file} → ${base}`)
      }
    }
    expect(
      stale,
      `These allowlisted utilities are no longer inert (fixed or gone) — remove them from ` +
        `src/design/inertUtilities.allowlist.json in the same commit:\n${stale.join('\n')}`,
    ).toEqual([])
  })
})
