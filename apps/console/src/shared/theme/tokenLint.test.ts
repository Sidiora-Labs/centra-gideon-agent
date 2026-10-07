import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { sourceViolations } from './tokenLintRule'
import ts from 'typescript'


// vitest runs from the web/ package dir; source lives in web/src.
const SRC = join(process.cwd(), "src")

const EXEMPT_DIRS = ['shared/theme/']
const EXEMPT_FILES = [
  'shared/ui/DotGlow.tsx',
  'shared/ui/GideonMark.tsx',
  'shared/ui/Spark.tsx',
  'shared/ui/WavyProgress.tsx',

  'features/files/fileMeta.ts',
  'shared/ui/content/registerBuiltins.ts',
  'shared/ui/content/exporters.ts',
  'features/terminal/TerminalView.tsx',
  'features/code/DiffReveal.tsx',
  'features/code/TypingReveal.tsx',
  'app/shell/appearance.tsx',
  'features/settings/settingsWidgets.tsx',
]

const ALLOWLIST = new Set<string>(loadAllowlist())

function loadAllowlist(): string[] {
  try {
    const raw = readFileSync(join(SRC, 'shared/theme/tokenLint.allowlist.json'), 'utf8')
    return JSON.parse(raw) as string[]
  } catch { return [] }
}

function walk(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    const rel = relative(SRC, p).replace(/\\/g, '/')
    if (EXEMPT_DIRS.some((d) => rel.startsWith(d))) continue
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

function authoredTokenSource(source: string): string {
  if (!/#[0-9a-fA-F]{3,8}\b/.test(source)) return source
  const file = ts.createSourceFile('tokens.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const nodes: ts.Node[] = []
  function visit(node: ts.Node) { nodes.push(node); ts.forEachChild(node, visit) }
  visit(file)
  const host = ts.createCompilerHost({ noLib: true })
  host.getSourceFile = name => name === file.fileName ? file : undefined
  const checker = ts.createProgram([file.fileName], { noLib: true }, host).getTypeChecker()
  const bound = (node: ts.Node) => ts.isIdentifier(node) ? checker.getSymbolAtLocation(node) : undefined
  const references = (declaration: ts.VariableDeclaration) => nodes.filter(n => ts.isIdentifier(n)
    && n !== declaration.name && bound(n) === bound(declaration.name))
  const opening = (node: ts.Node) => ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)
  const attribute = (node: ts.JsxOpeningElement | ts.JsxSelfClosingElement, name: string) => node.attributes.properties.find((p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText() === name)
  const expression = (a: ts.JsxAttribute | undefined) => a?.initializer && ts.isJsxExpression(a.initializer) ? a.initializer.expression : a?.initializer
  function ancestor(node: ts.Node, predicate: (n: ts.Node) => boolean): ts.Node | undefined {
    for (let current: ts.Node | undefined = node; current; current = current.parent) if (predicate(current)) return current
    return undefined
  }
  const property = (node: ts.ObjectLiteralExpression, name: string) => node.properties.find((p): p is ts.PropertyAssignment => ts.isPropertyAssignment(p) && p.name.getText() === name)
  function clinical(ref: ts.Node): boolean {
    const access = ref.parent
    if (!ts.isElementAccessExpression(access) || access.expression !== ref || !access.argumentExpression) return false
    const style = ancestor(access, n => ts.isJsxAttribute(n) && n.name.getText() === 'style')
    if (!style || !ts.isJsxAttribute(style)) return false
    const element = style.parent.parent
    const index = access.argumentExpression.getText().replace(/!$/, '')
    return opening(element) && expression(attribute(element, 'data-color'))?.getText() === index
      && /\.stimulus\.color$/.test(index) && ts.isJsxElement(element.parent)
      && element.parent.children.some(child => ts.isJsxExpression(child)
        && child.expression?.getText() === index.replace(/\.color$/, '.word'))
  }
  const chromeReference = (ref: ts.Node) => !!ancestor(ref, n => ts.isJsxAttribute(n) && n.name.getText() === 'style') && !clinical(ref)
  function isDataLiteral(node: ts.StringLiteral | ts.NoSubstitutionTemplateLiteral): boolean {
    if (ts.isJsxAttribute(node.parent) && node.parent.name.getText() === 'placeholder') return true
    const paint = ts.isJsxAttribute(node.parent) && ['fill', 'stopColor'].includes(node.parent.name.getText())
    if (paint) {
      const tag = node.parent.parent.parent
      const svg = ancestor(tag, n => ts.isJsxElement(n) && n.openingElement.tagName.getText() === 'svg')
      if (opening(tag) && svg && (tag.tagName.getText() === 'path' && !!attribute(tag, 'd')
        || tag.tagName.getText() === 'stop' && !!ancestor(tag, n => ts.isJsxElement(n) && n.openingElement.tagName.getText() === 'linearGradient'))) return true
    }
    const owner = ancestor(node, ts.isVariableDeclaration)
    if (owner && ts.isVariableDeclaration(owner)) {
      if (ts.isIdentifier(owner.name) && references(owner).some(chromeReference)) return false
      if (ts.isArrayBindingPattern(owner.name)) {
        const first = owner.name.elements[0]
        if (first && ts.isBindingElement(first) && ts.isIdentifier(first.name)) {
          const symbol = bound(first.name)
          if (nodes.some(n => ts.isIdentifier(n) && n !== first.name && bound(n) === symbol && chromeReference(n))) return false
        }
      }
    }
    const object = ts.isPropertyAssignment(node.parent) && node.parent.name.getText() === 'color' && ts.isObjectLiteralExpression(node.parent.parent) ? node.parent.parent : undefined
    if (object && ['shape', 'size', 'position', 'rotation'].every(key => property(object, key))) {
      const declaration = ancestor(object, ts.isVariableDeclaration)
      if (declaration && ts.isVariableDeclaration(declaration) && references(declaration).some(ref => ts.isCallExpression(ref.parent)
        && ref.parent.expression.getText() === 'JSON.stringify')) return true
    }
    if (object && property(object, 'tolerance') && object.properties.some(p => ts.isShorthandPropertyAssignment(p) && p.name.text === 'op')) {
      const branch = object.parent
      if (ts.isConditionalExpression(branch) && branch.whenTrue === object && ts.isBinaryExpression(branch.condition)
        && branch.condition.operatorToken.kind === ts.SyntaxKind.EqualsEqualsEqualsToken
        && branch.condition.left.getText() === 'op' && ts.isStringLiteral(branch.condition.right)
        && branch.condition.right.text === 'solid_background') return true
    }
    const declaration = ancestor(node, ts.isVariableDeclaration)
    if (!declaration || !ts.isVariableDeclaration(declaration)) return false
    if (ts.isIdentifier(declaration.name)) {
      const refs = references(declaration)
      if (refs.some(ref => ts.isJsxExpression(ref.parent) && ts.isJsxAttribute(ref.parent.parent)
        && ref.parent.parent.name.getText() === 'colorScale'
        && opening(ref.parent.parent.parent.parent) && !!attribute(ref.parent.parent.parent.parent, 'data'))) return true
      if (refs.some(clinical)) return true
    }
    if (ts.isArrayBindingPattern(declaration.name) && declaration.initializer && ts.isCallExpression(declaration.initializer)
      && declaration.initializer.expression.getText() === 'useState' && declaration.initializer.arguments.includes(node)) {
      const first = declaration.name.elements[0]
      if (!first || !ts.isBindingElement(first) || !ts.isIdentifier(first.name)) return false
      const symbol = bound(first.name)
      return nodes.some(n => opening(n) && n.tagName.getText() === 'input' && expression(attribute(n, 'type'))?.getText() === '"color"'
        && expression(attribute(n, 'value')) && bound(expression(attribute(n, 'value'))!) === symbol)
    }
    return false
  }
  const chars = source.split('')
  for (const node of nodes) {
    if (!(ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) || !/#[0-9a-fA-F]{3,8}\b/.test(node.text) || !isDataLiteral(node)) continue
    for (let i = node.getStart(file) + 1; i < node.end - 1; i++) if (chars[i] !== '\n' && chars[i] !== '\r') chars[i] = ' '
  }
  return chars.join('')
}

const violationsOf = (source: string) => sourceViolations(authoredTokenSource(source))

function violations(file: string): string[] {
  return violationsOf(readFileSync(file, 'utf8'))
}

describe('token-lint: design-system adherence', () => {
  const files = walk(SRC)

  it('preserves authored color contracts and rejects matching interface styling', () => {
    const authored = [
      '<TextInput placeholder="#112233, #aabbcc" />',
      '<svg viewBox="0 0 24 24"><path d="M0 0L24 24" fill="#D97757" /></svg>',
      '<svg viewBox="0 0 24 24"><defs><linearGradient id="mark"><stop stopColor="#4285F4" /></linearGradient></defs><path d="M0 0L24 24" /></svg>',
      'const scale = ["#ebedf0", "#2563eb"]; const view = <Graph data={observations} colorScale={scale} />',
      'const colors = {red: "#dc2626"}; const view = <p data-color={trial.stimulus.color} style={{color: colors[trial.stimulus.color]}}>{trial.stimulus.word}</p>',
      'const model = {parts:[{shape:"box",size:[1,1,1],position:[0,0,0],rotation:[0,0,0],color:"#5599cc"}]}; const json = JSON.stringify(model)',
      'const transform = op === "solid_background" ? {op, color:"#ffffff", tolerance:10} : {op}',
      'const [ink, setInk] = useState("#ff0000"); const view = <input type="color" value={ink} onChange={event => setInk(event.target.value)} />',
    ]
    const chrome = [
      '<TextInput style={{color:"#112233"}} />',
      '<div fill="#D97757" />',
      '<svg style={{background:"#4285F4"}}><path d="M0 0L24 24" /></svg>',
      'const scale = ["#ebedf0"]; const view = <Graph colorScale={scale} />',
      'const colors = {red:"#dc2626"}; const view = <p style={{color:colors.red}}>Status</p>',
      'const box = {shape:"box",color:"#5599cc"}; const json = JSON.stringify(box)',
      'const transform = {op:"resize",color:"#ffffff"}',
      'const [ink, setInk] = useState("#ff0000"); const view = <div style={{color:ink}} />',
      'const fallback = "#ffffff"',
      'const scale = ["#2563eb"]; const chart = <Graph data={observations} colorScale={scale} />; const chrome = <div style={{color:scale[0]}} />',
      'const colors = {red:"#dc2626"}; const trialView = <p data-color={trial.stimulus.color} style={{color:colors[trial.stimulus.color]}}>{trial.stimulus.word}</p>; const chrome = <div style={{color:colors.red}} />',
      'const [ink, setInk] = useState("#ff0000"); const picker = <input type="color" value={ink} />; const chrome = <div style={{color:ink}} />',

    ]
    for (const source of authored) expect(violationsOf(source), source).toEqual([])
    for (const source of chrome) expect(violationsOf(source), source).not.toEqual([])
    const adjacent = '<TextInput placeholder="#112233" style={{color:"#aabbcc"}} />'
    expect(violationsOf(adjacent)).toHaveLength(1)
  })

  it('finds source files to lint', () => {
    expect(files.length).toBeGreaterThan(100)
  })

  it('no raw hex/px outside design/ (except the shrinking allowlist)', () => {
    const stimulus = 'const ink = {red:"#dc2626"}; const trialView = <p data-color={trial.stimulus.color} style={{color:ink[trial.stimulus.color]}}>{trial.stimulus.word}</p>'
    expect(violationsOf(stimulus)).toEqual([])
    expect(violationsOf(stimulus.replace('{trial.stimulus.word}</p>', '{other.stimulus.word}</p>'))).not.toEqual([])
    expect(violationsOf(stimulus.replace('{trial.stimulus.word}</p>', '</p>'))).not.toEqual([])
    expect(violationsOf(stimulus + '; const chrome = <div style={{color:ink.red}} />')).not.toEqual([])
    const offenders: Record<string, string[]> = {}
    for (const f of files) {
      const rel = relative(SRC, f).replace(/\\/g, '/')
      if (EXEMPT_FILES.includes(rel) || ALLOWLIST.has(rel)) continue
      const v = violations(f)
      if (v.length) offenders[rel] = v
    }
    expect(offenders, `Raw hex/px found (route through tokens):\n${JSON.stringify(offenders, null, 2)}`).toEqual({})
  })

  it('allowlist only contains files that still have violations (no stale entries)', () => {
    const stale: string[] = []
    for (const rel of ALLOWLIST) {
      const full = join(SRC, rel)
      try {
        if (EXEMPT_FILES.includes(rel)) { stale.push(rel); continue }
        if (violations(full).length === 0) stale.push(rel)
      } catch { stale.push(rel) }
    }
    expect(stale, `These files are clean/gone — remove from the allowlist:\n${stale.join('\n')}`).toEqual([])
  })
})
