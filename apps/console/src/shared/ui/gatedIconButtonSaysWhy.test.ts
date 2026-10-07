import ts from 'typescript'
import { sourceFile, namedOwner } from '../testing/sourceOwners'
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { controlAvailability } from './controlState'
import { jsxTags } from '../testing/jsxContracts'

// three are genuine unavailability (a license gate, `disabled={false}`, an already-pinned widget).

const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

function gatedTags(src: string): string[] {
  const native = nativeControlBindings(src, ['IconButton', 'SquareIconButton'])
  if (!native.length) return []
  return jsxTags(src, native).filter((tag) => tag.attributes.has('disabled')).map((tag) => tag.tag)
}

describe('a gated icon button whose gate the user can fix says so', () => {
  const ADOPTERS: [string, RegExp][] = [
    ['features/code/CodeCockpitPage.tsx', /disabled=\{!text\.trim\(\)\} disabledReason=[\s\S]*?loading=\{busy\}/],
    ['features/code/CodeCockpitPage.tsx', /disabled=\{!text\.trim\(\)\} disabledReason=[\s\S]*?loading=\{sending\}/],
    ['shared/ui/FindBar.tsx', /label="Previous match"/],
    ['shared/ui/FindBar.tsx', /label="Next match"/],
  ]

  for (const [rel, gate] of ADOPTERS) {
    it(`${rel} ${gate.source.slice(0, 34)}… names what to do`, () => {
      const tag = gatedTags(readFileSync(join(SRC, rel), 'utf8')).find((t) => gate.test(t))
      expect(tag, `the gated button matching ${gate} must still exist`).toBeTruthy()
      expect(tag!, 'a fixable gate must say what fixes it').toMatch(/disabledReason=/)
    })
  }

  it('the cockpit reason is conditional, so it never fires mid-send', () => {
    const src = readFileSync(join(SRC, 'features/code/CodeCockpitPage.tsx'), 'utf8')
    const conditional = [...src.matchAll(/disabledReason=\{!text\.trim\(\) \? 'Type a steer first' : undefined\}/g)]
    expect(conditional.length, 'both steer composers gate the reason on the fixable branch only').toBe(2)
  })

  it('it converges on the canonical composer, which had it first', () => {
    const composer = readFileSync(join(SRC, 'shared/ui/Composer.tsx'), 'utf8')
    expect(composer).toContain('ComposerSend as AssistantComposerSend')
    const send = jsxTags(composer, ['AssistantComposerSend'])[0]
    expect(send.attributes.get('aria-disabled')).toBe("{action === 'send-disabled' || undefined}")
    expect(send.attributes.get('aria-description')).toBe('{sendReason}')
    expect(send.attributes.get('title')).toBe('{sendReason}')
    expect(send.attributes.get('onClick')).toBe('{primaryClick}')
    expect(namedOwner(composer, 'sendReason')).toContain("action === 'send-disabled'")
    expect(namedOwner(composer, 'primaryClick')).toContain('surface.action.canSubmit ? surface.submit : undefined')
    const surface = readFileSync(join(SRC, 'shared/ui/composer/useComposerSurface.ts'), 'utf8')
    expect(surface).toContain('if (!action.canSubmit) return')
    const native = readFileSync(join(SRC, 'shared/vendor/assistant-ui/elements/composer.tsx'), 'utf8')
    expect(namedOwner(native, 'ComposerSend')).toContain('{...props}')
  })

  it('a self-evident gate is still left mute — and every one left is a REAL gate', () => {
    // row explains itself by position, and a license-gated download by the badge beside it.
    const mute = walk(SRC).flatMap((abs) => gatedTags(readFileSync(abs, 'utf8')))
      .filter((t) => !/disabledReason=/.test(t))
    // ⚠️ note at the top): a license gate, `disabled={false}`, and an already-pinned widget.
    expect(mute.length, 'the self-evident gates keep their silence deliberately')
      .toBeGreaterThanOrEqual(3)
    const inFlight = mute.filter((t) => /(?<!aria-)disabled=\{[^}]*(?:busy|saving|sending|testing|rechecking|reconnecting|deleting|pending|loading)/i.test(t))
    expect(inFlight, 'an in-flight gate belongs on `loading`, not `disabled`').toEqual([])
  })

  it('both icon primitives keep the tab stop, which is what makes a reason audible at all', () => {
    for (const rel of ['shared/ui/IconButton.tsx', 'shared/ui/SquareIconButton.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src).toMatch(/controlAvailability\(disabled, loading, disabledReason, true\)/)
      const button = jsxTags(src, ['motion.button'])[0]
      expect(button.attributes.get('aria-disabled')).toBe('{state.ariaDisabled}')
      expect(button.attributes.has('disabled')).toBe(false)
      expect(src).toMatch(/activateControl\(event, state.blocked, onClick\)/)
      expect(controlAvailability(true, false, 'Choose a source', true)).toEqual({ blocked: true, nativeDisabled: false, ariaDisabled: true, busy: undefined })

    }
  })
})

function nativeControlBindings(source: string, symbols: readonly string[]): string[] {
  return sourceFile(source).statements.flatMap((statement) => {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier)) return []
    const module = statement.moduleSpecifier.text
    const named = statement.importClause?.namedBindings
    if (!named || !ts.isNamedImports(named)) return []
    return named.elements.filter((binding) => {
      const symbol = (binding.propertyName ?? binding.name).text
      return symbols.includes(symbol) && module.endsWith(`/${symbol}`)
    }).map((binding) => binding.name.text)
  })
}
