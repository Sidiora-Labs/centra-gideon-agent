import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { accentChip } from './accent'
import ts from 'typescript'


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })

describe('accentChip', () => {
  it('uses the container pair, not the accent as ink', () => {
    expect(accentChip.background).toBe('var(--color-primary-container)')
    expect(accentChip.color).toBe('var(--color-on-primary-container)')
  })

  it('carries no tint strength to drift', () => {
    expect(JSON.stringify(accentChip)).not.toMatch(/color-mix|%/)
  })
})

describe('no primary tint under primary ink survives', () => {
  const offenders: string[] = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    text.split('\n').forEach((line, i) => {
      const bg = /background:\s*'?`?color-mix\(in srgb, var\(--color-primary\) \d+%/.test(line)
      const ink = /color:\s*'?var\(--color-primary\)/.test(line)
      if (bg && ink) offenders.push(`${abs.slice(SRC.length + 1)}:${i + 1}`)
    })
  }

  it('has none left', () => {
    expect(
      offenders,
      `the accent is still both tint and ink (3.33–3.62:1 in light) at:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length).toBeGreaterThan(200)
  })
})



function textTintHits(source: string): number[] {
  const tree = ts.createSourceFile('consumer.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const hits: number[] = []
  const visit = (node: ts.Node) => {
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
      const opening = ts.isJsxElement(node) ? node.openingElement : node
      const tag = opening.tagName.getText(tree)
      const text = tag === 'input' || tag === 'textarea' || (ts.isJsxElement(node) && node.children.some(child =>
        ts.isJsxText(child) ? child.text.trim().length > 0 : ts.isJsxExpression(child) && !!child.expression))
      if (text) {
        const attr = opening.attributes.properties.find(property => ts.isJsxAttribute(property) && property.name.getText(tree) === 'className')
        const value = attr?.getText(tree) ?? ''
        if (/(?:^|[\s'"`])(?:hover:)?bg-primary\/\d+/.test(value) && /(?:^|[\s'"`])text-primary(?=$|[\s'"`])/.test(value))
          hits.push(tree.getLineAndCharacterOfPosition(opening.getStart(tree)).line + 1)
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(tree)
  return hits
}

describe('text consumers do not combine raw primary ink with a primary tint', () => {
  it('finds no remaining text consumer', () => {
    const offenders = walk(SRC).flatMap(abs => textTintHits(readFileSync(abs, 'utf8')).map(line => `${abs.slice(SRC.length + 1)}:${line}`))
    expect(offenders).toEqual([])
  })
  it('distinguishes raw ink, emphasis ink, and icon-only or nested elements', () => {
    expect(textTintHits('<button className="text-primary hover:bg-primary/10">Open</button>')).toEqual([1])
    expect(textTintHits('<input className={`bg-primary/5 ${formula ? \'text-primary\' : \'text-on-surface\'}`} />')).toEqual([1])
    expect(textTintHits('<button className="text-primary-emphasis bg-primary/10">Open</button>')).toEqual([])
    expect(textTintHits('<button className="text-primary bg-primary/10"><Plus /></button>')).toEqual([])
    expect(textTintHits('<span className="bg-primary/15"><Glyph className="text-primary" /></span>')).toEqual([])
  })
})


describe('the sweep actually adopted the shared definition', () => {
  const adopters = walk(SRC).filter((abs) => /\baccentChip\b/.test(readFileSync(abs, 'utf8')))

  it('is used across the tree, not in one corner', () => {
    expect(adopters.length, 'adopters of the shared accent chip').toBeGreaterThanOrEqual(20)
  })

  it('every adopter imports it rather than re-declaring the colours', () => {
    const bad = adopters
      .filter((abs) => !abs.endsWith(join('shared/theme', 'accent.ts')))
      .filter((abs) => !/import \{[^}]*accentChip[^}]*\} from '[^']*theme\/accent'/.test(readFileSync(abs, 'utf8')))
    expect(bad.map((b) => b.slice(SRC.length + 1)), 'uses accentChip without importing it').toEqual([])
  })
})


describe('the third spelling has a home', () => {
  it('is swept behaviourally next door, not silently ignored here', () => {
    expect(readFileSync(join(SRC, 'shared/theme/accentChipTone.test.tsx'), 'utf8'))
      .toMatch(/a rung chip inks coral through the container pair/)
  })
})

describe('no primary tint under CLASS-spelled primary ink survives', () => {
  const TINT = /color-mix\(in srgb, var\(--color-primary\) \d+%/g
  const INK_CLASS = /className="[^"]*\btext-primary\b[^"]*"/
  const offenders: string[] = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    for (const m of text.matchAll(TINT)) {
      const window = text.slice(Math.max(0, m.index! - 320), m.index!)
      if (INK_CLASS.test(window)) {
        offenders.push(`${abs.slice(SRC.length + 1)}:${text.slice(0, m.index!).split('\n').length}`)
      }
    }
  }

  it('has none left', () => {
    expect(
      offenders,
      `class-spelled accent ink over a primary tint (3.62:1 in light) at:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('the matcher still recognises the shape it polices — not vacuously green', () => {
    const sample = [
      '        <span className="shrink-0 rounded px-1.5 text-[0.75rem] text-primary"',
      "          style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>",
    ].join('\n')
    const hit = [...sample.matchAll(TINT)].some((m) => INK_CLASS.test(sample.slice(0, m.index!)))
    expect(hit, 'the detector matches the spelling that shipped three times').toBe(true)
    const benign = sample.replace(' text-primary', ' text-on-surface-low')
    const falsePositive = [...benign.matchAll(TINT)].some((m) => INK_CLASS.test(benign.slice(0, m.index!)))
    expect(falsePositive, 'a primary tint under non-accent ink is left alone').toBe(false)
  })
})
