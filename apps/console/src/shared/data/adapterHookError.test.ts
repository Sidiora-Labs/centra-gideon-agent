import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })
}

function adapterHooks(): { rel: string; name: string; body: string }[] {
  const out: { rel: string; name: string; body: string }[] = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')
    if (!src.includes('useQuery')) continue
    for (const m of src.matchAll(/export function (use[A-Z]\w*)\s*\([^)]*\)[^{]*\{/g)) {
      let i = m.index! + m[0].length
      let depth = 1
      while (i < src.length && depth > 0) {
        if (src[i] === '{') depth++
        else if (src[i] === '}') depth--
        i++
      }
      const body = src.slice(m.index! + m[0].length, i - 1)
      if (!/useQuery(?:<[\s\S]*?>)?\(/.test(body)) continue
      out.push({ rel: abs.slice(SRC.length + 1), name: m[1], body })
    }
  }
  return out
}

const EXEMPT: Record<string, string> = {
  'app/shell/usePlatform.ts:usePlatform': 'fail-closed by design — "" hides OS-gated UI',
}

describe('an adapter hook re-exposes useQuery\'s error', () => {
  const hooks = adapterHooks()

  it('finds the population — the scan is not vacuous', () => {
    expect(hooks.length, 'exported use* hooks that wrap useQuery').toBeGreaterThanOrEqual(4)
    expect(hooks.map((h) => h.name), 'the canonical one must be in the census').toContain('useAutonomyLadder')
  })

  const carriesError = (body: string) =>
    /const \{[^}]*\berror\b[^}]*\} = useQuery/.test(body)
    && /return \{[\s\S]*\berror\b/.test(body)
    && !/\berror:\s*(undefined|null)\b/.test(body)

  it('every adapter either returns the error or is named as an exemption', () => {
    const swallowing = hooks
      .filter((h) => !carriesError(h.body))
      .map((h) => `${h.rel}:${h.name}`)
      .filter((k) => !(k in EXEMPT))
      .sort()
    expect(swallowing, `these hide a failed read from every consumer:\n${swallowing.join('\n')}`).toEqual([])
  })

  it('the exemptions still exist and still look like their reason', () => {
    for (const key of Object.keys(EXEMPT)) {
      const [rel, name] = key.split(':')
      const hook = hooks.find((h) => h.rel === rel && h.name === name)
      expect(hook, `${key} left the census — prune the exemption`).toBeTruthy()
      expect(carriesError(hook!.body), `${key} now handles the error; drop its exemption`).toBe(false)
    }
  })

  it('the canonical adapter is the shape the others copy', () => {
    const ladder = hooks.find((h) => h.name === 'useAutonomyLadder')!
    expect(ladder.body, 'reads the error from the hook').toMatch(/const \{[^}]*\berror\b[^}]*\} = useQuery/)
    expect(ladder.body, 'and returns it').toMatch(/return \{[\s\S]*\berror\b/)
  })
})
