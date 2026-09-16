import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

function badgeTokens(): { file: string; token: string; uppercased: boolean }[] {
  const re = /className="([^"]*rounded-(?:pill|md|lg)[^"]*)"[^>]*>([a-z][a-z0-9_-]{2,19})</g
  const hits: { file: string; token: string; uppercased: boolean }[] = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')
    for (const m of src.matchAll(re)) {
      hits.push({
        file: abs.slice(SRC.length + 1),
        token: m[2],
        uppercased: /\buppercase\b/.test(m[1]),
      })
    }
  }
  return hits
}

const EXEMPT = new Set(['esc', 'manual', 'suppressed', 'multi-instance', 'span', 'div'])

const CONVERGED: [string, string][] = [
  ['builtin', 'Built-in'],
  ['archived', 'Archived'],
  ['disabled', 'Disabled'],
]

describe('state badges use the prose spelling the app already ships', () => {
  const tokens = badgeTokens()

  it('finds the badge population it is meant to police', () => {
    expect(tokens.length, 'bare-token badges found across web/src').toBeGreaterThanOrEqual(8)
  })

  it('no badge renders a lowercase token for a concept with a canonical spelling', () => {
    const offenders = tokens
      .filter((t) => !t.uppercased)
      .filter((t) => CONVERGED.some(([lower]) => t.token === lower))
      .map((t) => `${t.file}: "${t.token}"`)
    expect(offenders, 'use the prose form these files already use elsewhere').toEqual([])
  })

  it('each converged concept still ships its prose form as a visible label', () => {
    const all = walk(SRC).map((abs) => readFileSync(abs, 'utf8')).join('\n')
    for (const [lower, prose] of CONVERGED) {
      expect(all.includes(`>${prose}<`) || all.includes(`'${prose}'`), `${lower} → ${prose}`).toBe(true)
    }
  })

  it('the exemptions stay honest — each is still a lowercase badge with no prose form', () => {
    const seen = new Set(tokens.filter((t) => !t.uppercased).map((t) => t.token))
    const stale = [...EXEMPT].filter((t) => !['span', 'div'].includes(t) && !seen.has(t))
    expect(stale, 'exempt token no longer present as a lowercase badge — re-check whether it converged')
      .toEqual([])
  })
})
