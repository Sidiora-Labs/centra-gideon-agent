import { describe, it, expect } from 'vitest'
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

type Opening = ts.JsxOpeningElement | ts.JsxSelfClosingElement
const program = ts.createProgram(walk(SRC), { jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler, module: ts.ModuleKind.ESNext })
let checker = program.getTypeChecker()
const attr = (node: Opening, name: string) => node.attributes.properties.find((p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText() === name)
const value = (a: ts.JsxAttribute | undefined): ts.Node | undefined => a?.initializer && ts.isJsxExpression(a.initializer) ? a.initializer.expression : a?.initializer
function descendants(node: ts.Node): ts.Node[] {
  const result: ts.Node[] = []
  function visit(n: ts.Node) { result.push(n); ts.forEachChild(n, visit) }
  visit(node)
  return result
}
const openings = (node: ts.Node) => descendants(node).filter((n): n is Opening => ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n))
function text(node: ts.Node | undefined): string {
  if (!node) return ''
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text
  return node.getText()
}
function contains(node: ts.Node | undefined, symbol: ts.Symbol | undefined): boolean {
  return !!node && !!symbol && descendants(node).some(n => ts.isIdentifier(n) && checker.getSymbolAtLocation(n) === symbol)
}
function unavailable(node: Opening): boolean {
  const classes = text(value(attr(node, 'className'))).split(/\s+/)
  const hidden = attr(node, 'hidden')
  const tab = text(value(attr(node, 'tabIndex')))
  return !!hidden && text(value(hidden)) !== 'false' || classes.some(c => /^(?:\w+:)*(?:hidden|invisible)$/.test(c)) || /^-\d+$/.test(tab)
    || /(?:display:\s*['"]none|visibility:\s*['"]hidden)/.test(text(value(attr(node, 'style'))))
}
function declaration(node: ts.Node): ts.Node | undefined {
  let symbol = checker.getSymbolAtLocation(node)
  if (symbol?.flags && symbol.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol)
  const d = symbol?.valueDeclaration
  const implementation = d && ts.isVariableDeclaration(d) ? d.initializer : d
  if (implementation && ts.isCallExpression(implementation)) {
    const wrapper = checker.getSymbolAtLocation(implementation.expression)?.declarations?.find(ts.isImportSpecifier)
    const imported = wrapper?.parent.parent.parent
    if (wrapper && (wrapper.propertyName?.text || wrapper.name.text) === 'forwardRef'
      && imported && ts.isImportDeclaration(imported) && ts.isStringLiteral(imported.moduleSpecifier)
      && imported.moduleSpecifier.text === 'react') {
      return implementation.arguments.find(n => ts.isFunctionExpression(n) || ts.isArrowFunction(n))
    }
  }
  return implementation
}
function parameterSymbol(component: ts.Node, prop: string): ts.Symbol | undefined {
  if (!ts.isFunctionLike(component)) return undefined
  for (const parameter of component.parameters) {
    if (ts.isObjectBindingPattern(parameter.name)) {
      const entry = parameter.name.elements.find(e => (e.propertyName?.getText() || e.name.getText()) === prop)
      if (entry) return checker.getSymbolAtLocation(entry.name)
    }
  }
  return undefined
}
function named(node: Opening): boolean {
  if (['aria-label', 'ariaLabel', 'label'].some(key => text(value(attr(node, key))).trim())) return true
  const parent = node.parent
  return ts.isJsxElement(parent) && parent.children.some(child => ts.isJsxText(child) && child.text.trim()
    || ts.isJsxExpression(child) && !!child.expression && !/^undefined|null|false$/.test(child.expression.getText())
    || ts.isJsxElement(child) && descendants(child).some(n => ts.isJsxText(n) && n.text.trim() || ts.isJsxExpression(n) && !!n.expression))
}
function forwards(component: ts.Node, prop: string, seen = new Set<ts.Node>()): boolean {
  if (seen.has(component)) return false
  seen = new Set(seen).add(component)
  const symbol = parameterSymbol(component, prop)
  if (!symbol) return false
  for (const opening of openings(component)) {
    if (unavailable(opening)) continue
    const handler = value(attr(opening, 'onClick'))
    const tag = opening.tagName.getText()
    if ((tag === 'button' || tag.endsWith('.button')) && named(opening) && contains(handler, symbol)) return true
    const target = declaration(opening.tagName)
    if (target) for (const a of opening.attributes.properties) {
      if (ts.isJsxAttribute(a) && contains(value(a), symbol) && forwards(target, a.name.getText(), seen)) return true
    }
  }
  // Local render helpers must receive this exact callback, and render a named button.
  for (const call of descendants(component).filter(ts.isCallExpression)) {
    const helper = declaration(call.expression)
    if (!helper || !ts.isFunctionLike(helper)) continue
    for (let i = 0; i < call.arguments.length; i++) {
      const param = helper.parameters[i]
      if (!param || !contains(call.arguments[i], symbol)) continue
      const bound = checker.getSymbolAtLocation(param.name)
      if (openings(helper).some(n => !unavailable(n) && (n.tagName.getText() === 'button' || n.tagName.getText().endsWith('.button')) && named(n) && contains(value(attr(n, 'onClick')), bound))) return true
    }
  }
  return false
}
function clicksRef(node: ts.Node | undefined, ref: ts.Symbol): boolean {
  return !!node && descendants(node).some(n => ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression)
    && n.expression.name.text === 'click' && ts.isPropertyAccessExpression(n.expression.expression)
    && n.expression.expression.name.text === 'current' && checker.getSymbolAtLocation(n.expression.expression.expression) === ref)
}
function reachable(input: Opening, source: ts.SourceFile): boolean {
  if (!unavailable(input)) return true
  const refNode = value(attr(input, 'ref'))
  const ref = refNode && checker.getSymbolAtLocation(refNode)
  if (!ref) return false
  for (const control of openings(source)) {
    if (unavailable(control)) continue
    for (const a of control.attributes.properties) {
      if (!ts.isJsxAttribute(a)) continue
      const callback = value(a)
      if (clicksRef(callback, ref)) {
        if (control.tagName.getText() === 'button' && a.name.getText() === 'onClick' && named(control)) return true
        const target = declaration(control.tagName)
        if (target && forwards(target, a.name.getText())) return true
      }
      // Named menu entries must be passed to a component that renders their label and callback together.
      for (const entry of callback ? descendants(callback).filter(ts.isObjectLiteralExpression) : []) {
        const label = entry.properties.find(p => ts.isPropertyAssignment(p) && p.name.getText() === 'label')
        const click = entry.properties.find((p): p is ts.PropertyAssignment => ts.isPropertyAssignment(p) && p.name.getText() === 'onClick')
        if (!label || !click || !clicksRef(click.initializer, ref)) continue
        const target = declaration(control.tagName)
        const items = target && parameterSymbol(target, a.name.getText())
        if (!target || !items) continue
        for (const call of descendants(target).filter(ts.isCallExpression)) {
          if (!ts.isPropertyAccessExpression(call.expression) || call.expression.name.text !== 'map' || !contains(call.expression.expression, items)) continue
          const render = call.arguments[0]
          if (!render || !ts.isArrowFunction(render)) continue
          const item = render.parameters[0] && checker.getSymbolAtLocation(render.parameters[0].name)
          if (openings(render).some(n => n.tagName.getText() === 'button' && !unavailable(n)
            && contains(value(attr(n, 'onClick')), item) && named(n))) return true
        }
      }
    }
  }
  return false
}
const pickers = () => program.getSourceFiles().filter(s => s.fileName.startsWith(SRC) && !/\.(test|doc)\.tsx$/.test(s.fileName)).flatMap(src => openings(src)
  .filter(tag => tag.tagName.getText() === 'input' && text(value(attr(tag, 'type'))) === 'file')
  .map(tag => ({ file: src.fileName.slice(SRC.length + 1), tag, src })))

describe('every file picker can be reached without a mouse', () => {
  it('distinguishes visible inputs and bound named controls from broken forwarding', () => {
    const cases: [string, boolean][] = [
      ['<input type="file" />', true],
      ['<input type="file" className="overflow-hidden" />', true],
      ['<input type="file" className="sr-only" />', true],
      ['<input type="file" hidden />', false],
      ['<input type="file" style={{ display: "none" }} />', false],
      ['<input type="file" tabIndex={-1} />', false],
      ['<><input type="file" hidden ref={file} /><button onClick={() => other.current?.click()}>Upload</button></>', false],
      ['<><input type="file" hidden ref={file} /><button onClick={() => file.current?.click()} /></>', false],
      ['<><input type="file" hidden ref={file} /><button hidden onClick={() => file.current?.click()}>Upload</button></>', false],
      ['<><input type="file" hidden ref={file} /><button onClick={() => file.current?.click()}>Upload</button></>', true],
      ['<><input type="file" hidden ref={file} /><div aria-label="Upload" onClick={() => file.current?.click()} /></>', false],
      ['<><input type="file" hidden ref={file} /><Broken label="Upload" onClick={() => file.current?.click()} /></>', false],
    ]
    const original = checker
    try {
      for (const [jsx, expected] of cases) {
        const filename = join(SRC, 'picker-control.tsx')
        const source = ts.createSourceFile(filename, `const file = {current: document.createElement('input')}; const other = {current: document.createElement('input')}; function Broken({label}: {label:string;onClick:()=>void}) { return <button>{label}</button> }; const view = (${jsx});`, ts.ScriptTarget.ESNext, true, ts.ScriptKind.TSX)
        const host = ts.createCompilerHost({})
        const getSourceFile = host.getSourceFile.bind(host)
        host.getSourceFile = (name, version, error, fresh) => name === filename ? source : getSourceFile(name, version, error, fresh)
        checker = ts.createProgram([filename], { jsx: ts.JsxEmit.ReactJSX }, host).getTypeChecker()
        const input = openings(source).find(n => n.tagName.getText() === 'input')!
        expect(reachable(input, source), jsx).toBe(expected)
      }
    } finally { checker = original }
  })

  it('finds the population (not vacuously green)', () => {
    expect(pickers().length, 'the file-input census must not go empty').toBeGreaterThanOrEqual(7)
  })

  it('is either focusable itself, or forwarded to by a named real control', () => {
    const bad: string[] = []
    for (const { file, tag, src } of pickers()) {
      if (reachable(tag, src)) continue
      bad.push(`${file}: hidden picker with no focusable input and no named forwarding control`)
    }
    expect(bad, `a file picker is pointer-only:\n${bad.join('\n')}`).toEqual([])
  })

  it('the two fixed this cycle keep their focusable input AND a visible focus ring', () => {
    const cases: [string, RegExp][] = [
      ['features/knowledge/KnowledgeCreatePage.tsx', /border-dashed[\s\S]{0,200}?has-\[input:focus-visible\]:ring-2/],
      ['features/loop/LoopComposer.tsx', /<label[\s\S]{0,300}?has-\[input:focus-visible\]:ring-2/],
    ]
    for (const [rel, ring] of cases) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      expect(code, `${rel}: the picker must stay focusable`).toMatch(/type="file"[\s\S]{0,220}?className="sr-only"/)
      expect(code, `${rel}: a visually hidden input needs the ring drawn on its container`).toMatch(ring)
      expect(code, `${rel}: the input must sit inside the element carrying the ring`)
        .toMatch(/has-\[input:focus-visible\]:ring-primary\b[\s\S]{0,400}?type="file"/)
    }
  })

  it('the knowledge drop area guards the re-entrant click', () => {
    const code = readFileSync(join(SRC, 'features/knowledge/KnowledgeCreatePage.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code).toMatch(/if \(e\.target === fileRef\.current\) return/)
  })

  it('the knowledge copy no longer says only "click"', () => {
    const src = readFileSync(join(SRC, 'features/knowledge/KnowledgeCreatePage.tsx'), 'utf8')
    expect(src).not.toMatch(/or click to choose/)
    expect(src).toMatch(/or choose one/)
  })
})
