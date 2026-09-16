import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const strip = (s: string) => s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx?$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

const RETRY_SITES = [
  'features/artifacts/ArtifactViewer.tsx',
  'features/files/browse/FileViewer.tsx',
  'features/code/CodeCockpitPage.tsx',
]

describe('the retry button carries its accent through a variant, not a className', () => {
  it('the variant exists and uses the emphasis shade', () => {
    const btn = read('shared/ui/Button.tsx')
    expect(btn).toMatch(/'ghost-accent': 'bg-transparent text-primary-emphasis hover:bg-surface-high'/)
    expect(btn, 'and it is a declared Variant').toMatch(/\| 'ghost-accent'/)
  })

  it('all six sites use it', () => {
    let n = 0
    for (const rel of [...RETRY_SITES, 'features/ChatPage.tsx']) {
      const code = strip(read(rel))
      n += [...code.matchAll(/<Button variant="ghost-accent"/g)].length
    }
    expect(n, 'converged accent-ghost buttons').toBe(6)
  })

  it('no Button anywhere pushes the ACCENT through className any more', () => {
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      const code = strip(readFileSync(abs, 'utf8')).replace(/=>/g, '\u21d2')
      for (const m of code.matchAll(/<Button[^>]*className="[^"]*\btext-primary\b[^"]*"/g))
        offenders.push(`${abs.replace(SRC, '')}: ${m[0].slice(0, 60)}`)
    }
    expect(offenders, 'Buttons inking the accent through className').toEqual([])
  })

  it('and that sweep is not vacuous — it sees a planted offender', () => {
    const planted = '<Button variant="ghost" size="xs" onClick={() \u21d2 reload()} className="mt-1 text-primary">'
    expect([...planted.matchAll(/<Button[^>]*className="[^"]*\btext-primary\b[^"]*"/g)].length).toBe(1)
  })

  it('the ghost variant itself is untouched — the blast-radius floor', () => {
    expect(read('shared/ui/Button.tsx')).toMatch(/ghost: 'bg-transparent text-on-surface hover:bg-surface-high'/)
  })

  it('the variant is documented, and the trap with it', () => {
    const doc = read('shared/ui/Button.doc.ts')
    expect(doc).toMatch(/ghost-accent/)
    expect(doc, 'the Do-not entry').toMatch(/Do not push a colour through className/)
  })

  it('the measurement rides with the variant, not just the token', () => {
    expect(read('shared/ui/Button.tsx')).toMatch(/4\.37:1/)
  })

  it('Segmented is still deliberately deferred — not silently swept in', () => {
    const seg = read('shared/ui/Segmented.tsx')
    expect(seg, 'Segmented still tints an option tone').toMatch(/color-mix\(in srgb, \$\{o\.tone\} 20%, transparent\)/)
  })
})
