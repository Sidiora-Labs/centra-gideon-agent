import ts from 'typescript'
import { jsxTags } from '../../shared/testing/jsxContracts'
import { sourceFile } from '../../shared/testing/sourceOwners'
import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ToggleRow } from './settingsUI'


const patchNoop = () => vi.fn()

describe('the danger glyph says what it means', () => {
  it('is named while ON', () => {
    const { container } = render(
      <ToggleRow label="YOLO mode" cfg={{ f: true }} field="f" patch={patchNoop() as never} danger />,
    )
    const glyph = container.querySelector('svg.text-warn')
    expect(glyph, 'the danger glyph must render while ON').not.toBeNull()
    expect(glyph!.getAttribute('aria-label'), 'it must say what being ON means').toMatch(/safety default/i)
    expect(glyph!.getAttribute('role'), 'role="img" is what makes the name stick').toBe('img')
  })

  it('says nothing while OFF, because there is nothing to say', () => {
    const { container } = render(
      <ToggleRow label="YOLO mode" cfg={{ f: false }} field="f" patch={patchNoop() as never} danger />,
    )
    expect(container.querySelector('svg.text-warn')).toBeNull()
    expect(container.querySelector('[aria-label*="safety default"]')).toBeNull()
  })

  it('a non-danger row gains no name — the four plain panels are untouched', () => {
    const { container } = render(
      <ToggleRow label="Poll" cfg={{ f: true }} field="f" patch={patchNoop() as never} />,
    )
    expect(container.querySelector('svg.text-warn')).toBeNull()
    expect(container.querySelector('[aria-label*="safety default"]')).toBeNull()
  })

  it("the label is the prop's own doc sentence, so the two cannot drift", () => {
    const src = readFileSync(join(import.meta.dirname, "settingsUI.tsx"), 'utf8')
    const at = src.indexOf('danger?: boolean')
    expect(at, 'the danger prop moved — this rail measures nothing').toBeGreaterThan(-1)
    const doc = src.slice(Math.max(0, at - 300), at)
    expect(doc, "the prop's doc must still describe a relaxed safety default").toMatch(/safety default/i)
  })
})

describe('every consumer that relaxes a safety default marks itself', () => {
  const PANELS = join(import.meta.dirname)
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
    })

  it('the danger consumers are a real, findable population (vacuity floor)', () => {
    let consumers = 0
    for (const abs of walk(PANELS)) {
      const src = readFileSync(abs, 'utf8')
      for (const tag of jsxTags(src, nativeBindings(src, 'ToggleRow'))) {
        if (tag.attributes.has('danger') && tag.attributes.get('danger') !== '{false}') consumers++
      }
    }
    expect(consumers, 'no ToggleRow passes `danger` — the glyph is unreachable').toBeGreaterThanOrEqual(2)
  })
})

function nativeBindings(src: string, symbol: string, module = './settingsUI'): string[] {
  return sourceFile(src).statements.flatMap(statement => {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier) || statement.moduleSpecifier.text !== module) return []
    const bindings = statement.importClause?.namedBindings
    return bindings && ts.isNamedImports(bindings) ? bindings.elements.filter(binding => (binding.propertyName ?? binding.name).text === symbol).map(binding => binding.name.text) : []
  })
}
