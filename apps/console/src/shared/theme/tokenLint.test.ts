import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { sourceViolations } from './tokenLintRule'


// vitest runs from the web/ package dir; source lives in web/src.
const SRC = join(process.cwd(), "src")

const EXEMPT_DIRS = ['shared/theme/']
const EXEMPT_FILES = [
  'shared/ui/DotGlow.tsx',
  'shared/ui/GideonMark.tsx',
  'shared/ui/Spark.tsx',
  'shared/ui/WavyProgress.tsx',

  'features/files/fileMeta.ts',
  'shared/ui/content/registerBuiltins.ts',
  'shared/ui/content/exporters.ts',
  'features/terminal/TerminalView.tsx',
  'features/code/DiffReveal.tsx',
  'features/code/TypingReveal.tsx',
  'app/shell/appearance.tsx',
  'features/settings/settingsWidgets.tsx',
]

const ALLOWLIST = new Set<string>(loadAllowlist())

function loadAllowlist(): string[] {
  try {
    const raw = readFileSync(join(SRC, 'shared/theme/tokenLint.allowlist.json'), 'utf8')
    return JSON.parse(raw) as string[]
  } catch { return [] }
}

function walk(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    const rel = relative(SRC, p).replace(/\\/g, '/')
    if (EXEMPT_DIRS.some((d) => rel.startsWith(d))) continue
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

function violations(file: string): string[] {
  return sourceViolations(readFileSync(file, 'utf8'))
}

describe('token-lint: design-system adherence', () => {
  const files = walk(SRC)

  it('finds source files to lint', () => {
    expect(files.length).toBeGreaterThan(100)
  })

  it('no raw hex/px outside design/ (except the shrinking allowlist)', () => {
    const offenders: Record<string, string[]> = {}
    for (const f of files) {
      const rel = relative(SRC, f).replace(/\\/g, '/')
      if (EXEMPT_FILES.includes(rel) || ALLOWLIST.has(rel)) continue
      const v = violations(f)
      if (v.length) offenders[rel] = v
    }
    expect(offenders, `Raw hex/px found (route through tokens):\n${JSON.stringify(offenders, null, 2)}`).toEqual({})
  })

  it('allowlist only contains files that still have violations (no stale entries)', () => {
    const stale: string[] = []
    for (const rel of ALLOWLIST) {
      const full = join(SRC, rel)
      try {
        if (EXEMPT_FILES.includes(rel)) { stale.push(rel); continue }
        if (violations(full).length === 0) stale.push(rel)
      } catch { stale.push(rel) }
    }
    expect(stale, `These files are clean/gone — remove from the allowlist:\n${stale.join('\n')}`).toEqual([])
  })
})
