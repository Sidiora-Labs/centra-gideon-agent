import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import ts from 'typescript'


const SRC = join(process.cwd(), "src")

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const REVEALS_ON_FOCUS =
  /focus-within:opacity-100|group-focus-within(?:\/[\w-]+)?:opacity-100|focus-visible:opacity-100|group-focus(?:\/[\w-]+)?:opacity-100/

interface Hit { file: string; line: number; cls: string; ok: boolean }

function hoverRevealed(src: string, rel: string): Hit[] {
  const out: Hit[] = []
  const ast = ts.createSourceFile(rel, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const decorative = new Set<number>()
  const icons = new Set(ast.statements.filter(ts.isImportDeclaration).flatMap(declaration => {
    const bindings = declaration.importClause?.namedBindings
    return ts.isStringLiteral(declaration.moduleSpecifier) && declaration.moduleSpecifier.text === 'lucide-react' && bindings && ts.isNamedImports(bindings)
      ? bindings.elements.map(binding => binding.name.text) : []
  }))
  const visit = (node: ts.Node) => {
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && icons.has(node.tagName.getText(ast)) && !node.attributes.properties.some(a => ts.isJsxAttribute(a) && ['tabIndex', 'onClick', 'onKeyDown'].includes(a.name.getText(ast)))) {
      for (const attribute of node.attributes.properties) if (ts.isJsxAttribute(attribute) && attribute.name.getText(ast) === 'className') decorative.add(attribute.getStart(ast))
    }
    ts.forEachChild(node, visit)
  }
  visit(ast)
  for (const m of src.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
    if (decorative.has(m.index!)) continue
    const cls = m[1] ?? m[2] ?? ''
    if (!/\bopacity-0\b/.test(cls)) continue
    if (!/group-hover(?:\/[\w-]+)?:opacity-100/.test(cls)) continue
    if (/pointer-events-none/.test(cls)) continue
    out.push({
      file: rel,
      line: src.slice(0, m.index).split('\n').length,
      cls: cls.replace(/\s+/g, ' '),
      ok: REVEALS_ON_FOCUS.test(cls),
    })
  }
  return out
}

const all = walk(SRC).flatMap((abs) => hoverRevealed(strip(readFileSync(abs, 'utf8')), abs.slice(SRC.length + 1)))

describe('the rail: hover-revealed means focus-revealed', () => {
  it('every hover-revealed focusable container also reveals on focus', () => {
    const offenders = all.filter((h) => !h.ok).map((h) => `${h.file}:${h.line}  ${h.cls.slice(0, 90)}`)
    expect(
      offenders,
      `opacity-0 does NOT remove focusability, so Tab lands on an invisible control:\n  ` +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('the rail is not vacuously green — it finds the hover-revealed containers', () => {
    expect(all.length, 'the scanner must find the tree\'s hover-revealed controls').toBeGreaterThan(20)

    const named = all.filter((h) => /group-hover\/[\w-]+:opacity-100/.test(h.cls))
    expect(named.length, 'named Tailwind groups must be scanned').toBeGreaterThan(0)

    const bad = hoverRevealed('<div className="opacity-0 group-hover:opacity-100" />', 'x.tsx')
    expect(bad.length).toBe(1)
    expect(bad[0].ok).toBe(false)
    expect(hoverRevealed('<div className="pointer-events-none opacity-0 group-hover:opacity-100" />', 'x.tsx').length).toBe(0)
  })

  it('recognizes named focus groups while retaining interactive icon checks', () => {
    expect(hoverRevealed('<div className="opacity-0 group-hover/message:opacity-100 group-focus-within/message:opacity-100" />', 'x.tsx')[0].ok).toBe(true)
    const icon = 'import { ArrowUpRightIcon } from "lucide-react"; '
    expect(hoverRevealed(icon + '<ArrowUpRightIcon className="opacity-0 group-hover:opacity-100" />', 'x.tsx')).toEqual([])
    expect(hoverRevealed(icon + '<ArrowUpRightIcon tabIndex={0} className="opacity-0 group-hover:opacity-100" />', 'x.tsx')[0].ok).toBe(false)
    expect(hoverRevealed('<CustomIcon className="opacity-0 group-hover:opacity-100" />', 'x.tsx')[0].ok).toBe(false)
  })

  it('both accepted forms count as a focus reveal', () => {
    for (const cls of [
      'opacity-0 group-hover:opacity-100 focus-within:opacity-100',
      'opacity-0 group-hover:opacity-100 focus-visible:opacity-100',
    ]) {
      expect(hoverRevealed(`<div className="${cls}" />`, 'x.tsx')[0].ok, cls).toBe(true)
    }
  })
})
