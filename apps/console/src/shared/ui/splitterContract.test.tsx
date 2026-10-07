import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import ts from 'typescript'

const SRC = join(process.cwd(), 'src')
const walk = (directory: string): string[] => readdirSync(directory).flatMap(name => {
  const path = join(directory, name)
  if (statSync(path).isDirectory()) return walk(path)
  return /\.tsx$/.test(name) && !/\.(test|doc)\.tsx$/.test(name) ? [path] : []
})

function handles(source: string) {
  const file = ts.createSourceFile('component.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const found: { text: string; focusText: string; names: Set<string>; delegated: boolean }[] = []
  const visit = (node: ts.Node) => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const attributes = node.attributes.properties
      const names = new Set(attributes.filter(ts.isJsxAttribute).map(attribute => attribute.name.getText(file)))
      const role = attributes.find(attribute => ts.isJsxAttribute(attribute) && attribute.name.getText(file) === 'role')
      const separator = role && ts.isJsxAttribute(role) && role.initializer && ts.isStringLiteral(role.initializer) && role.initializer.text === 'separator'
      const interactive = names.has('tabIndex') || names.has('aria-valuenow') || ['onPointerDown', 'onMouseDown', 'onKeyDown'].some(name => names.has(name))
      if (separator && interactive) found.push({ text: node.getText(file), focusText: ts.isJsxElement(node.parent) ? node.parent.getText(file) : node.getText(file), names,
        delegated: attributes.some(attribute => ts.isJsxSpreadAttribute(attribute) && attribute.expression.getText(file) === 'surface.resizeBindings') })
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return found
}

const claimants = () => walk(SRC).flatMap(path => {
  const source = readFileSync(path, 'utf8')
  const elements = handles(source)
  return elements.length ? [{ rel: path.slice(SRC.length + 1), source, elements }] : []
})

function hasKeyboardDelegate(rel: string, source: string) {
  if (rel !== 'shared/ui/Composer.tsx') return false
  const hook = readFileSync(join(SRC, 'shared/ui/composer/useComposerSurface.ts'), 'utf8')
  return /useComposerSurface/.test(source)
    && /resizeBindings:\s*\{\s*onPointerDown,\s*onKeyDown\s*\}/.test(hook)
    && /const onKeyDown\s*=/.test(hook)
    && /keyboardComposerHeight\(event.key, height, event.shiftKey\)/.test(hook)
    && /event.preventDefault\(\)/.test(hook)
    && /setHeight\(next\)/.test(hook)
}

describe('every actual interactive splitter implements its own splitter contract', () => {
  it('finds all native interactive claimants without counting decorative separators', () => {
    expect(claimants().map(file => file.rel).sort()).toEqual([
      'features/chat/ChatFilePanel.tsx', 'features/chat/SessionWorkspace.tsx',
      'features/code/CodeCockpitPage.tsx', 'features/terminal/TerminalDrawer.tsx',
      'shared/ui/Composer.tsx', 'shared/ui/NavRail.tsx', 'shared/ui/SidePanel.tsx',
    ])
    const decorative = readFileSync(join(SRC, 'shared/vendor/assistant-ui/ui-compat.tsx'), 'utf8')
    expect(decorative).toContain('role="separator"')
    expect(handles(decorative)).toHaveLength(0)
  })

  it('each individual handle is focusable, keyboard-operable and reports its current bounds', () => {
    const bad: string[] = []
    for (const { rel, source, elements } of claimants()) {
      for (const element of elements) {
        const missing = [
          /tabIndex=\{0\}/.test(element.text) ? '' : 'not focusable',
          element.names.has('onKeyDown') || (element.delegated && hasKeyboardDelegate(rel, source)) ? '' : 'no actual key handler',
          element.names.has('aria-valuenow') ? '' : 'no current value',
          element.names.has('aria-valuemin') && element.names.has('aria-valuemax') ? '' : 'no min/max',
        ].filter(Boolean)
        if (missing.length) bad.push(`${rel}: ${missing.join(', ')}`)
      }
    }
    expect(bad, `Every interactive separator must be operable:\n${bad.join('\n')}`).toEqual([])
  })

  it('each handle describes the existing arrow-key interaction', () => {
    for (const { rel, elements } of claimants()) for (const element of elements) {
      expect(element.text, `${rel} must name its interaction`).toMatch(/aria-label=\{?[`"']Resize [^`"']*arrow keys/)
    }
  })

  it('each focused handle has a visible primary seam or focus ring', () => {
    for (const { rel, elements } of claimants()) for (const element of elements) {
      const direct = /focus-visible:(?:bg|ring)-primary/.test(element.text)
      const descendant = /className="[^"]*\bgroup(?:\/[\w-]+)?\b/.test(element.text)
        && /group-focus-visible(?:\/[\w-]+)?:(?:bg|ring)-primary/.test(element.focusText)
      expect(direct || descendant, `${rel} needs visible keyboard focus on its own handle or grouped child`).toBe(true)
    }
  })

  it('detects the inaccessible interactive NavRail shape without borrowing attributes from another handle', () => {
    const old = handles('<div role="separator" aria-orientation="vertical" onMouseDown={() => {}} />')
    expect(old).toHaveLength(1)
    expect(old[0].names.has('tabIndex')).toBe(false)
    expect(old[0].names.has('aria-valuenow')).toBe(false)
    expect(handles('<span role="separator" aria-orientation="horizontal" />')).toHaveLength(0)
  })
})
