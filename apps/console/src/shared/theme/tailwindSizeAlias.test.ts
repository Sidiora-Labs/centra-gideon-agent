import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

const SIZE_ALIASES = ['xs', 'sm', 'base', 'lg', 'xl', '2xl', '3xl', '4xl', '5xl', '6xl', '7xl', '8xl', '9xl']
const ALIAS_RE = new RegExp(String.raw`(?<![-\w])text-(${SIZE_ALIASES.join('|')})(?![-\w])`)

const ALLOWED: Record<string, string> = {}

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) { out.push(...walk(p)); continue }
    if (/\.(tsx|ts)$/.test(name) && !/\.test\.(tsx|ts)$/.test(name)) out.push(p)
  }
  return out
}

describe('type sizes stay on the app ramp, not Tailwind aliases', () => {
  it('no page or ui file uses a text-<size> alias', () => {
    const offenders: string[] = []
    for (const abs of [...walk(join(SRC, "features")), ...walk(join(SRC, "shared/ui"))]) {
      const rel = abs.slice(SRC.length + 1)
      if (rel in ALLOWED) continue
      const src = readFileSync(abs, 'utf8')
      src.split('\n').forEach((line, i) => {
        if (ALIAS_RE.test(line)) offenders.push(`${rel}:${i + 1}`)
      })
    }
    expect(
      offenders,
      `Tailwind font-size alias(es) found — use an explicit ramp value (e.g. text-[0.8125rem]):\n  ` +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('the regex matches a real alias', () => {
    expect(ALIAS_RE.test('className="text-on-surface-low text-sm"')).toBe(true)
    expect(ALIAS_RE.test('className="p-3 text-base"')).toBe(true)
    expect(ALIAS_RE.test('className="text-2xl"')).toBe(true)
  })

  it('the regex does NOT match colour tokens or explicit ramp values', () => {
    expect(ALIAS_RE.test('className="text-on-surface-low"')).toBe(false)
    expect(ALIAS_RE.test('className="text-[0.8125rem]"')).toBe(false)
    expect(ALIAS_RE.test('className="text-success text-[0.75rem]"')).toBe(false)
    expect(ALIAS_RE.test('className="text-center"')).toBe(false)
    expect(ALIAS_RE.test('data-type="title-l"')).toBe(false)
  })
})
