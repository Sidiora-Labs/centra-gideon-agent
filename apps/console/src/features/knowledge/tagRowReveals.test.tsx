import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = readFileSync(join(process.cwd(), "src/features/knowledge/TagManager.tsx"), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

describe('a truncated tag name is still recoverable', () => {
  it('the name carries its full value in a title', () => {
    expect(CODE).toMatch(/className="min-w-0 flex-1 truncate[^"]*"[^>]*title=\{tag\.name\}/)
  })

  it('and it is still the element that truncates — the pair is the point', () => {
    expect(CODE, 'still truncates').toMatch(/truncate/)
  })

  it('matches how the rest of the app labels a truncating span', () => {
    const sibling = readFileSync(join(process.cwd(), "src/shared/ui/SystemWidget.tsx"), 'utf8')
    expect(sibling, 'the idiom this follows still ships').toMatch(/truncate[^"]*"\s+title=\{/)
  })
})

describe('the hint names both routes to the menu', () => {
  it('keeps the pointer gesture and adds the keyboard one', () => {
    expect(CODE).toMatch(/Right-click a tag to nest, merge, or delete it/)
    expect(CODE).toMatch(/Tab to one and press Shift\+F10/)
  })

  it('the keyboard route it now promises is really wired', () => {
    const primitive = readFileSync(join(process.cwd(), "src/shared/ui/motion/ContextMenu.tsx"), 'utf8')
    expect(primitive, 'Shift+F10 and the ContextMenu key open it')
      .toMatch(/e\.key === 'ContextMenu' \|\| \(e\.key === 'F10' && e\.shiftKey\)/)
    expect(CODE, 'the row is wrapped in the primitive').toMatch(/<ContextMenu key=\{tag\.id\} items=\{menu\}>/)
  })

  it('the actions it names are the ones the menu offers — the vacuity floor', () => {
    expect(CODE, 'nest').toMatch(/label: `Nest under \$\{o\.name\}`/)
    expect(CODE, 'merge').toMatch(/label: `Merge into \$\{o\.name\}`/)
    expect(CODE, 'delete').toMatch(/label: 'Delete', danger: true/)
  })

  it('the unused-tag sentence is untouched', () => {
    expect(CODE).toMatch(/An unused tag is kept — it stays part of your taxonomy/)
  })
})
