import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

function code(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(ts|tsx|css)$/.test(name)) out.push(p)
  }
  return out
}

describe('colour scheme is inherited, never pinned on a control', () => {
  const files = walk(SRC)

  it('reads a real tree (not vacuously green)', () => {
    expect(files.length).toBeGreaterThan(200)
    expect(files.some((f) => f.endsWith('shared/ui/forms.tsx'))).toBe(true)
  })

  it('no file pins color-scheme on an element', () => {
    const offenders = files
      .filter((f) => /\[color-scheme:\s*(dark|light)\]/.test(code(readFileSync(f, 'utf8'))))
      .map((f) => f.slice(SRC.length + 1))
    expect(offenders, `these pin a scheme instead of inheriting the theme's: ${offenders.join(', ')}`).toEqual([])
  })

  it('the form primitives keep the rest of their chrome', () => {
    const forms = readFileSync(join(SRC, 'shared/ui/forms.tsx'), 'utf8')
    const select = forms.slice(forms.indexOf('export function Select'))
    expect(select).toMatch(/bg-surface-container/)
    expect(select).toMatch(/focus:ring-2/)
    expect(select).toMatch(/rounded-md/)
  })

  it('the docs no longer advertise a pinned dark scheme', () => {
    const doc = readFileSync(join(SRC, 'shared/ui/forms.doc.ts'), 'utf8')
    expect(doc).not.toMatch(/color-scheme:\s*dark/)
    expect(doc, 'and it should say what happens instead').toMatch(/theme-inherited color-scheme/)
  })

  it('the root still owns the scheme per theme', () => {
    const tokens = readFileSync(join(SRC, 'shared/theme/tokens.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
    expect(tokens).toMatch(/\.light\s*\{[\s\S]*?color-scheme:\s*light/)
    expect(tokens).toMatch(/:root:not\(\.light\)\s*\{\s*color-scheme:\s*dark/)
  })
})
