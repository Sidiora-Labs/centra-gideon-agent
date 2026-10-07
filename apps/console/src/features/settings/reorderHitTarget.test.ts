import ts from 'typescript'
import { jsxTags } from '../../shared/testing/jsxContracts'
import { sourceFile } from '../../shared/testing/sourceOwners'
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const routing = () => readFileSync(join(SRC, 'features/settings/RoutingPanel.tsx'), 'utf8')

function moveButtons(src: string): string[] {
  return jsxTags(src, nativeBindings(src, 'Button', '../../shared/ui/Button'))
    .filter(tag => tag.attributes.get('ariaLabel')?.startsWith('{`Move '))
    .map(tag => tag.element)
}

describe('reorder buttons carry a 24px+ target', () => {
  it('finds both reorder buttons (not vacuously green)', () => {
    expect(moveButtons(routing()).length, 'both the earlier and later button must be matched').toBe(2)
  })

  it('each uses the 28px square geometry, not p-1', () => {
    for (const tag of moveButtons(routing())) {
      expect(tag, 'must be a 28px grid-centred square').toMatch(/grid size-7 place-items-center/)
      expect(/\bp-1\b/.test(tag), '21px padding-only geometry must not come back').toBe(false)
    }
  })

  it('the size matches the icon-button primitive it borrows from', () => {
    const sib = readFileSync(join(SRC, 'shared/ui/SquareIconButton.tsx'), 'utf8')
    const primitive = jsxTags(sib, ['motion.button'])
    expect(primitive).toHaveLength(1)
    const classes = primitive[0].attributes.get('className')!
    for (const token of ['grid', 'size-7', 'place-items-center', 'rounded-md']) expect(classes).toMatch(new RegExp(`\\b${token}\\b`))
  })

  it('the busy semantics are preserved by the native control-state contract', () => {
    const src = routing()
    const buttons = moveButtons(src)
    expect(buttons[0]).toMatch(/disabled=\{\(i === 0\) \|\| \(busy\)\}/)
    expect(buttons[1]).toMatch(/disabled=\{\(i === shown\.length - 1\) \|\| \(busy\)\}/)
    for (const button of buttons) expect(button).toMatch(/disabledReason=\{\(busy\) \? '[^']+' : 'Already tried (first|last)'\}/)
    const primitive = readFileSync(join(SRC, 'shared/ui/Button.tsx'), 'utf8')
    expect(primitive).toMatch(/disabled=\{state\.nativeDisabled\}/)
    expect(primitive).toMatch(/activateControl\(event, state\.blocked, onClick\)/)
  })

  it('each button still names itself and hides its icon', () => {
    for (const tag of moveButtons(routing())) expect(tag).toMatch(/ariaLabel=\{`Move \$\{ref\} (earlier|later)`\}/)
    expect(routing()).toMatch(/<ArrowUp size=\{13\} aria-hidden \/>/)
    expect(routing()).toMatch(/<ArrowDown size=\{13\} aria-hidden \/>/)
  })
})

function nativeBindings(src: string, symbol: string, module = './settingsUI'): string[] {
  return sourceFile(src).statements.flatMap(statement => {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier) || statement.moduleSpecifier.text !== module) return []
    const bindings = statement.importClause?.namedBindings
    return bindings && ts.isNamedImports(bindings) ? bindings.elements.filter(binding => (binding.propertyName ?? binding.name).text === symbol).map(binding => binding.name.text) : []
  })
}
