import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

function walk(dir: string): string[] {
  const out: string[] = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) out.push(...walk(p))
    else if (/\.tsx?$/.test(e.name) && !e.name.includes('.test.')) out.push(p)
  }
  return out
}

function whatValues(): { rel: string; value: string }[] {
  const out: { rel: string; value: string }[] = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')
    for (const m of src.matchAll(/<LoadError\b[^>]*?\bwhat=(?:"([^"]*)"|\{([^}]*)\})/gs)) {
      const rel = abs.slice(SRC.length + 1)
      if (m[1] != null) out.push({ rel, value: m[1] })
      else for (const lit of (m[2] ?? '').matchAll(/'([^']+)'|"([^"]+)"/g)) out.push({ rel, value: lit[1] ?? lit[2] })
    }
  }
  return out
}

describe("LoadError's what composes a grammatical headline", () => {
  const values = whatValues()

  it('finds the population — the scan is not vacuous', () => {
    expect(values.length, 'LoadError what= values across the tree').toBeGreaterThanOrEqual(45)
  })

  it('no value carries a leading article — the headline always renders', () => {
    const bad = values.filter((v) => /^(the|this|a|an)\s/i.test(v.value))
      .map((v) => `${v.rel}: "Couldn't load your ${v.value}"`)
    expect(bad, `an article makes the headline ungrammatical:\n${bad.join('\n')}`).toEqual([])
  })

  it('every value is lowercase-leading unless it is a proper noun', () => {
    const shouty = values.filter((v) => /^[A-Z]{2,}/.test(v.value)).map((v) => `${v.rel}: ${v.value}`)
    expect(shouty, 'no SHOUTING nouns').toEqual([])
  })

  it('the fallback reassurance no longer interpolates the noun', () => {
    const scaffold = readFileSync(join(SRC, 'shared/ui/ListScaffold.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const body = /The server didn't respond[^`"]*/.exec(scaffold)?.[0] ?? ''
    expect(body, 'the fallback must still exist').toContain('load error')
    expect(body, 'and must not put a caller noun into "are safe"').not.toMatch(/\$\{\s*what/)
    expect(scaffold, 'no "<noun> are safe" survives in the primitive CODE').not.toMatch(/\$\{what\}\s+are safe/)
  })
})
