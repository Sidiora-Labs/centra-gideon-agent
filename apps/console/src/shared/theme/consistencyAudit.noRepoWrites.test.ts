
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, resolve, dirname } from 'node:path'
import { createHash } from 'node:crypto'
import ts from 'typescript'
import { WRITE_ENV } from './consistencyAudit.generate.test'

const SRC = join(process.cwd(), "src")

const FS_WRITE = /\b(writeFileSync|appendFileSync|mkdirSync|rmSync|rmdirSync|unlinkSync|cpSync|copyFileSync|renameSync|writeFile|appendFile|mkdir|rm|rmdir|unlink|cp|copyFile|rename|createWriteStream)\s*\(/

const MAY_WRITE: Record<string, string> = {
  'shared/theme/consistencyAudit.generate.test.ts':
    'The generator for the committed drift inventory, gated on ' +
    `${WRITE_ENV}=1 and invoked only by \`npm run audit:consistency\`. A plain test ` +
    'run skips it, so the tree stays clean.',
}

function testFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...testFiles(p))
    else if (/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

type NativePathContract = { entry: string; module?: string; key: string; homeBound?: boolean; sources: Record<string, string> }
const nativePathContracts: NativePathContract[] = [
  {
    "entry": "checks/runtime/capabilities/workspace/ui_server.py",
    "key": "repo",
    "sources": {
      "checks/runtime/capabilities/workspace/ui_server.py": "85025f4273d189a3dbaff270aefe35c0f007a851021522e197d3e0ce0d2a5316",
      "checks/runtime/capabilities/workspace/test_workspace.py": "93f4efdd673d9290a32819b6cd5148c252db196c8ad0eb5614ed7e769a636799",
      "runtime/gideon/core/config/loader.py": "7c8bb4b424e855189d1d393fbdb5e93c05b41c76c389525ce3bbf2de39e651b6",
      "runtime/gideon/core/config/locations.py": "f30e882605677778f4ceb7463c80a15653eea6e73ddffd65ba4e1ce274087270"
    }
  },
  {
    "entry": "checks/runtime/capabilities/experience/serve_game_assets_ui.py",
    "module": "checks.runtime.capabilities.experience.serve_game_assets_ui",
    "key": "sprite_path",
    "homeBound": true,
    "sources": {
      "checks/runtime/capabilities/experience/serve_game_assets_ui.py": "2249bb6363dccf7df3e63f7fb64847d014a649dc73e4d39b4770eccee556e303",
      "runtime/gideon/workspace/artifacts/native.py": "bfa6785ff03b4ea9f0c0f3ae2d4bbe084dd51dd554d432d7fce5c37d2bb189c6"
    }
  }
]

function unsafeWrites(source: string, filename = join(SRC, 'example.test.tsx')): string[] {
  const file = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  // Avoid binding the entire suite when a file has no imported filesystem writer.
  const writerNames = new Set<string>(), fsNamespaces = new Set<string>()
  const mutation = /^(writeFileSync|appendFileSync|mkdirSync|rmSync|rmdirSync|unlinkSync|cpSync|copyFileSync|renameSync|writeFile|appendFile|mkdir|rm|rmdir|unlink|cp|copyFile|rename|createWriteStream)$/
  for (const declaration of file.statements.filter(ts.isImportDeclaration)) {
    if (!ts.isStringLiteral(declaration.moduleSpecifier) || !/^(?:node:)?fs(?:\/promises)?$/.test(declaration.moduleSpecifier.text)) continue
    const bindings = declaration.importClause?.namedBindings
    if (declaration.importClause?.name) fsNamespaces.add(declaration.importClause.name.text)
    if (bindings && ts.isNamespaceImport(bindings)) fsNamespaces.add(bindings.name.text)
    if (bindings && ts.isNamedImports(bindings)) for (const binding of bindings.elements) if (mutation.test(binding.propertyName?.text ?? binding.name.text)) writerNames.add(binding.name.text)
  }
  let hasWriter = false
  const findWriter = (node: ts.Node) => {
    if (ts.isCallExpression(node)) {
      const call = node.expression
      if (ts.isIdentifier(call) && writerNames.has(call.text) || ts.isPropertyAccessExpression(call) && ts.isIdentifier(call.expression) && fsNamespaces.has(call.expression.text) && mutation.test(call.name.text)) hasWriter = true
    }
    ts.forEachChild(node, findWriter)
  }
  findWriter(file)
  if (!hasWriter) return []
  const host = ts.createCompilerHost({ noLib: true, noResolve: true })
  host.getSourceFile = name => name === filename ? file : undefined
  const checker = ts.createProgram([filename], { noLib: true, noResolve: true }, host).getTypeChecker()
  const symbol = (node: ts.Node) => ts.isShorthandPropertyAssignment(node.parent) ? checker.getShorthandAssignmentValueSymbol(node.parent) : checker.getSymbolAtLocation(node)
  const imported = new Map<ts.Symbol, { module: string; name: string; path?: string }>()
  const namespaces = new Map<ts.Symbol, string>()
  const values = new Map<ts.Symbol, ts.Expression[]>()
  const loops = new Map<ts.Symbol, ts.Expression>()
  const destructured = new Map<ts.Symbol, { from: ts.Expression; key: string }>()
  const assignments = new Map<ts.Symbol, ts.BinaryExpression[]>()
  const functions = new Map<ts.Symbol, ts.FunctionDeclaration>()
  const normalize = (module: string) => module.replace(/^fs(?=\/|$)/, 'node:fs').replace(/^path$/, 'node:path').replace(/^os$/, 'node:os')
  for (const declaration of file.statements.filter(ts.isImportDeclaration)) {
    if (!ts.isStringLiteral(declaration.moduleSpecifier)) continue
    const module = normalize(declaration.moduleSpecifier.text)
    const bindings = declaration.importClause?.namedBindings
    const defaultSymbol = declaration.importClause?.name && symbol(declaration.importClause.name)
    if (defaultSymbol) namespaces.set(defaultSymbol, module)
    if (bindings && ts.isNamedImports(bindings)) for (const binding of bindings.elements) {
      const identity = symbol(binding.name)
      if (identity) imported.set(identity, { module, name: binding.propertyName?.text ?? binding.name.text,
        path: module.startsWith('.') ? resolve(dirname(filename), module) + '.ts' : undefined })
    }
    if (bindings && ts.isNamespaceImport(bindings)) {
      const identity = symbol(bindings.name)
      if (identity) namespaces.set(identity, module)
    }
  }
  const resolvedCall = (expression: ts.Expression) => {
    const identity = symbol(expression)
    if (ts.isIdentifier(expression) && identity) return imported.get(identity)
    if (ts.isPropertyAccessExpression(expression)) {
      const owner = symbol(expression.expression)
      if (owner && namespaces.has(owner)) return { module: namespaces.get(owner)!, name: expression.name.text }
    }
    return undefined
  }
  const collect = (node: ts.Node) => {
    if (ts.isVariableDeclaration(node) && node.initializer) {
      if (ts.isIdentifier(node.name)) {
        const identity = symbol(node.name)
        if (identity) values.set(identity, [...(values.get(identity) ?? []), node.initializer])
      } else if (ts.isObjectBindingPattern(node.name)) for (const binding of node.name.elements) {
        const identity = symbol(binding.name)
        if (identity) destructured.set(identity, { from: node.initializer, key: (binding.propertyName ?? binding.name).getText(file) })
      }
    }
    if (ts.isForOfStatement(node) && ts.isVariableDeclarationList(node.initializer)) for (const declaration of node.initializer.declarations) {
      const identity = symbol(declaration.name)
      if (identity) loops.set(identity, node.expression)
    }
    if (ts.isBinaryExpression(node) && [ts.SyntaxKind.EqualsToken, ts.SyntaxKind.PlusEqualsToken].includes(node.operatorToken.kind) && ts.isIdentifier(node.left)) {
      const identity = symbol(node.left)
      if (identity) {
        values.set(identity, [...(values.get(identity) ?? []), node.right])
        if (node.operatorToken.kind === ts.SyntaxKind.EqualsToken) assignments.set(identity, [...(assignments.get(identity) ?? []), node])
      }
    }
    if (ts.isFunctionDeclaration(node) && node.name) {
      const identity = symbol(node.name)
      if (identity) functions.set(identity, node)
    }
    ts.forEachChild(node, collect)
  }
  collect(file)
  const unwrap = (node: ts.Expression): ts.Expression => ts.isAwaitExpression(node) || ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isNonNullExpression(node) ? unwrap(node.expression) : node
  const hookOf = (node: ts.Node): ts.CallExpression | undefined => {
    for (let parent = node.parent; parent; parent = parent.parent) if (ts.isCallExpression(parent)) {
      const call = resolvedCall(parent.expression)
      if (call?.module === 'vitest' && ['beforeAll', 'beforeEach', 'it', 'test', 'afterAll', 'afterEach'].includes(call.name)) return parent
    }
    return undefined
  }
  const staticPath = (node: ts.Expression | undefined, seen = new Set<ts.Symbol>()): string | undefined => {
    if (!node) return undefined
    node = unwrap(node)
    if (ts.isStringLiteral(node)) return node.text
    if (node.getText(file) === 'process.cwd()') return process.cwd()
    if (node.getText(file) === 'import.meta.dirname') return dirname(filename)
    const identity = symbol(node)
    if (ts.isIdentifier(node) && identity && !seen.has(identity)) {
      const options = values.get(identity)?.map(value => staticPath(value, new Set([...seen, identity])))
      return options?.length && options.every(value => value === options[0]) ? options[0] : undefined
    }
    if (ts.isCallExpression(node)) {
      const call = resolvedCall(node.expression)
      const arguments_ = node.arguments.map(argument => staticPath(argument, seen))
      if (call?.module === 'node:path' && arguments_.every((argument): argument is string => argument !== undefined)) {
        if (call.name === 'resolve') return resolve(...arguments_)
        if (call.name === 'join') return join(...arguments_)
        if (call.name === 'dirname' && arguments_[0]) return dirname(arguments_[0])
      }
    }
    return undefined
  }
  const stringValues = (node: ts.Expression | undefined, seen = new Set<ts.Symbol>()): string[] | undefined => {
    if (!node) return undefined
    node = unwrap(node)
    if (ts.isStringLiteral(node)) return [node.text]
    if (ts.isArrayLiteralExpression(node)) {
      const parts = node.elements.map(element => ts.isExpression(element) ? stringValues(element, seen) : undefined)
      return parts.every((part): part is string[] => !!part) ? parts.flat() : undefined
    }
    const identity = symbol(node)
    if (ts.isIdentifier(node) && identity && !seen.has(identity)) {
      const next = new Set([...seen, identity])
      if (loops.has(identity)) return stringValues(loops.get(identity), next)
      const binding = imported.get(identity)
      if (binding?.path) {
        const importedFile = ts.createSourceFile(binding.path, readFileSync(binding.path, 'utf8'), ts.ScriptTarget.Latest, true)
        const constants = new Map<string, ts.Expression>()
        for (const statement of importedFile.statements) if (ts.isVariableStatement(statement)) for (const declaration of statement.declarationList.declarations) {
          if (ts.isIdentifier(declaration.name) && declaration.initializer) constants.set(declaration.name.text, declaration.initializer)
        }
        const literal = (expression: ts.Expression, visited = new Set<string>()): string[] | undefined => {
          expression = unwrap(expression)
          if (ts.isStringLiteral(expression)) return [expression.text]
          if (ts.isIdentifier(expression) && !visited.has(expression.text)) {
            const value = constants.get(expression.text)
            return value && literal(value, new Set([...visited, expression.text]))
          }
          if (ts.isArrayLiteralExpression(expression)) {
            const parts = expression.elements.map(element => ts.isExpression(element) ? literal(element, visited) : undefined)
            return parts.every((part): part is string[] => !!part) ? parts.flat() : undefined
          }
          return undefined
        }
        const value = constants.get(binding.name)
        return value && literal(value)
      }
      const parts = values.get(identity)?.map(value => stringValues(value, next))
      return parts?.length && parts.every((part): part is string[] => !!part) ? parts.flat() : undefined
    }
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) && node.expression.name.text === 'filter') {
      const options = stringValues(node.expression.expression, seen)
      const predicate = node.arguments[0]
      if (options && predicate && ts.isArrowFunction(predicate) && ts.isBinaryExpression(predicate.body) && predicate.body.operatorToken.kind === ts.SyntaxKind.ExclamationEqualsEqualsToken && ts.isStringLiteral(predicate.body.right)) {
        const excluded = predicate.body.right.text
        return options.filter(value => value !== excluded)
      }
    }
    return undefined
  }
  let consumer: ts.CallExpression | undefined
  const tempDepth = (node: ts.Expression | undefined, seen = new Set<ts.Symbol>()): number | undefined => {
    if (!node) return undefined
    node = unwrap(node)
    const identity = symbol(node)
    if (ts.isIdentifier(node) && identity && !seen.has(identity)) {
      const next = new Set([...seen, identity]), options = values.get(identity)
      const from = destructured.get(identity)
      if (from) return returnedDepth(from.from, from.key, next)
      if (!options?.length) return undefined
      const actual = options.filter(value => !(ts.isStringLiteral(value) && value.text === ''))
      if (actual.length !== options.length) {
        const context = consumer && hookOf(consumer)
        const call = context && resolvedCall(context.expression)
        const setup = assignments.get(identity)?.find(assignment => {
          const hook = hookOf(assignment), name = hook && resolvedCall(hook.expression)?.name
          return name === 'beforeAll' && (call?.name === 'it' || call?.name === 'test' || context === hook && consumer!.getStart(file) > assignment.end)
        })
        if (!setup) return undefined
      }
      const depths = actual.map(value => tempDepth(value, next))
      return depths.length && depths.every((depth): depth is number => depth !== undefined) ? Math.min(...depths) : undefined
    }
    if (ts.isPropertyAccessExpression(node)) return returnedDepth(node.expression, node.name.text, seen)
    if (ts.isCallExpression(node)) {
      const call = resolvedCall(node.expression)
      if (node.expression.getText(file) === 'Promise.resolve') return tempDepth(node.arguments[0], seen)
      if (call && ['node:fs', 'node:fs/promises'].includes(call.module) && /^(mkdtempSync|mkdtemp)$/.test(call.name)) {
        const prefix = node.arguments[0] && unwrap(node.arguments[0])
        if (prefix && ts.isTemplateExpression(prefix) && !prefix.head.text && prefix.templateSpans.length === 1) {
          const span = prefix.templateSpans[0], tmp = ts.isCallExpression(span.expression) && resolvedCall(span.expression.expression)
          if (tmp && tmp.module === 'node:os' && tmp.name === 'tmpdir' && /^\/[^/]+$/.test(span.literal.text) && !span.literal.text.includes('..')) return 0
        }
        if (prefix && ts.isCallExpression(prefix)) {
          const pathCall = resolvedCall(prefix.expression), first = prefix.arguments[0]
          const tmp = first && ts.isCallExpression(first) && resolvedCall(first.expression)
          const suffixes = prefix.arguments.slice(1).map(argument => stringValues(argument))
          if (pathCall?.module === 'node:path' && ['join', 'resolve'].includes(pathCall.name) && tmp && tmp.module === 'node:os' && tmp.name === 'tmpdir' && suffixes.length && suffixes.every(part => part?.every(value => !!value && !value.includes('..') && !value.startsWith('/')))) return 0
        }
      }
      if (call?.module === 'node:path') {
        const depth = tempDepth(node.arguments[0], seen)
        if (depth === undefined) return undefined
        if (call.name === 'dirname') return depth > 0 ? depth - 1 : undefined
        if (['join', 'resolve'].includes(call.name)) {
          const suffixes = node.arguments.slice(1).map(argument => stringValues(argument))
          if (suffixes.every(part => part?.every(value => !value.split(/[\\/]/).includes('..') && (call.name === 'join' || !value.startsWith('/'))))) return depth + suffixes.reduce((count, part) => count + Math.min(...part!.map(value => value.split('/').filter(Boolean).length)), 0)
        }
      }
    }
    if (ts.isTemplateExpression(node) && !node.head.text && node.templateSpans.length === 1) {
      const span = node.templateSpans[0], depth = tempDepth(span.expression, seen)
      if (depth !== undefined && span.literal.text.startsWith('/') && !span.literal.text.split(/[\\/]/).includes('..')) return depth + span.literal.text.split('/').filter(Boolean).length
    }
    if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken && ts.isStringLiteral(node.right) && !node.right.text.includes('..')) return tempDepth(node.left, seen)
    return undefined
  }
  const streamDerived = (node: ts.Expression | undefined, input: ts.Symbol, seen = new Set<ts.Symbol>()): number | undefined => {
    if (!node) return undefined
    node = unwrap(node)
    if (ts.isStringLiteral(node)) return node.text === '' ? 0 : undefined
    const identity = symbol(node)
    if (ts.isIdentifier(node) && identity) {
      if (identity === input) return 1
      if (seen.has(identity)) return undefined
      const parts = values.get(identity)?.map(value => streamDerived(value, input, new Set([...seen, identity])))
      return parts?.length && parts.every((part): part is number => part !== undefined) ? Math.max(...parts) : undefined
    }
    if (ts.isCallExpression(node)) {
      if (node.expression.getText(file) === 'String' && !symbol(node.expression)) return streamDerived(node.arguments[0], input, seen)
      if (ts.isPropertyAccessExpression(node.expression) && ['split', 'find', 'toString', 'trim'].includes(node.expression.name.text)) return streamDerived(node.expression.expression, input, seen)
    }
    return undefined
  }
  const nativeTicket = (node: ts.Expression, seen = new Set<ts.Symbol>()): NativePathContract | undefined => {
    node = unwrap(node)
    const identity = symbol(node)
    if (ts.isIdentifier(node) && identity && !seen.has(identity)) {
      const options = values.get(identity)?.map(value => nativeTicket(value, new Set([...seen, identity])))
      return options?.length && options.every(value => value === options[0]) ? options[0] : undefined
    }
    if (ts.isNewExpression(node) && node.expression.getText(file) === 'Promise') {
      const executor = node.arguments?.[0]
      if (executor && (ts.isArrowFunction(executor) || ts.isFunctionExpression(executor)) && executor.parameters[0]) {
        const resolveIdentity = symbol(executor.parameters[0].name), results: NativePathContract[] = []
        let unknown = false
        const visit = (child: ts.Node) => {
          if (ts.isCallExpression(child) && symbol(child.expression) === resolveIdentity) {
            const ticket = child.arguments[0] && nativeTicket(child.arguments[0], seen)
            if (ticket) results.push(ticket); else unknown = true
          }
          ts.forEachChild(child, visit)
        }
        visit(executor.body)
        if (!unknown && results.length && results.every(result => result === results[0])) return results[0]
      }
    }
    if (!ts.isCallExpression(node) || node.expression.getText(file) !== 'JSON.parse') return undefined
    for (let parent = node.parent; parent; parent = parent.parent) {
      if (!ts.isCallExpression(parent) || !ts.isPropertyAccessExpression(parent.expression) || parent.expression.name.text !== 'on' || !parent.arguments[0] || !ts.isStringLiteral(parent.arguments[0]) || parent.arguments[0].text !== 'data') continue
      const callback = parent.arguments[1]
      if (!callback || !(ts.isArrowFunction(callback) || ts.isFunctionExpression(callback)) || !callback.parameters[0]) continue
      const input = symbol(callback.parameters[0].name)
      if (!input || streamDerived(node.arguments[0], input) !== 1) continue
      const stream = unwrap(parent.expression.expression)
      if (!ts.isPropertyAccessExpression(stream) || stream.name.text !== 'stdout') continue
      const child = symbol(stream.expression), starts = child && values.get(child)
      if (!starts || starts.length !== 1) continue
      const start = unwrap(starts[0])
      if (!ts.isCallExpression(start) || resolvedCall(start.expression)?.module !== 'node:child_process' || resolvedCall(start.expression)?.name !== 'spawn') continue
      const interpreter = start.arguments[0]
      const python = (expression: ts.Expression | undefined): boolean => {
        if (!expression) return false
        expression = unwrap(expression)
        if (ts.isStringLiteral(expression)) return /^(?:python3?|.*\/python3?)$/.test(expression.text)
        if (expression.getText(file) === 'process.env.GIDEON_TEST_PYTHON') return true
        if (ts.isBinaryExpression(expression) && expression.operatorToken.kind === ts.SyntaxKind.BarBarToken) return python(expression.left) && python(expression.right)
        const path = staticPath(expression)
        return path === resolve(SRC, '../../..', '.venv/bin/python')
      }
      if (!python(interpreter)) continue
      const arguments_ = start.arguments[1], options = start.arguments[2]
      if (!arguments_ || !ts.isArrayLiteralExpression(arguments_) || !options || !ts.isObjectLiteralExpression(options)) continue
      const cwd = options.properties.find(property => ts.isPropertyAssignment(property) && property.name.getText(file) === 'cwd')
      if (!cwd || !ts.isPropertyAssignment(cwd) || options.properties.slice(options.properties.indexOf(cwd) + 1).some(ts.isSpreadAssignment) || staticPath(cwd.initializer) !== resolve(SRC, '../../..')) continue
      const contract = nativePathContracts.find(candidate => arguments_.elements.some(argument => ts.isExpression(argument) && staticPath(argument) === resolve(SRC, '../../..', candidate.entry))
        || candidate.module && arguments_.elements.some((argument, index) => {
          const previous = arguments_.elements[index - 1]
          return ts.isStringLiteral(argument) && argument.text === candidate.module && previous && ts.isStringLiteral(previous) && previous.text === '-m'
        }))
      if (!contract || !Object.entries(contract.sources).every(([path, hash]) => createHash('sha256').update(readFileSync(resolve(SRC, '../../..', path))).digest('hex') === hash)) continue
      if (contract.homeBound) {
        const env = options.properties.find(property => ts.isPropertyAssignment(property) && property.name.getText(file) === 'env')
        if (!env || !ts.isPropertyAssignment(env) || !ts.isObjectLiteralExpression(env.initializer)) continue
        const home = env.initializer.properties.find(property => ts.isPropertyAssignment(property) && property.name.getText(file) === 'GIDEON_HOME')
        if (!home || !ts.isPropertyAssignment(home) || env.initializer.properties.slice(env.initializer.properties.indexOf(home) + 1).some(ts.isSpreadAssignment) || tempDepth(home.initializer) === undefined) continue
      }
      return contract
    }
    return undefined
  }
  const returnedDepth = (node: ts.Expression, key: string, seen: Set<ts.Symbol>): number | undefined => {
    node = unwrap(node)
    const ticket = nativeTicket(node)
    if (ticket?.key === key) return 1
    if (ts.isCallExpression(node)) {
      const identity = symbol(node.expression), fn = identity && functions.get(identity)
      if (fn?.body) {
        const results: number[] = []; let unknown = false
        const visit = (child: ts.Node) => {
          if (ts.isReturnStatement(child)) {
            if (!child.expression || !ts.isObjectLiteralExpression(child.expression)) { unknown = true; return }
            const property = child.expression.properties.find(part => part.name?.getText(file) === key)
            const value = property && (ts.isPropertyAssignment(property) ? property.initializer : ts.isShorthandPropertyAssignment(property) ? property.name : undefined)
            const depth = value && tempDepth(value, seen)
            if (depth === undefined) unknown = true; else results.push(depth)
          } else if (!ts.isFunctionLike(child)) ts.forEachChild(child, visit)
        }
        ts.forEachChild(fn.body, visit)
        if (!unknown && results.length) return Math.min(...results)
      }
    }
    return undefined
  }
  const out: string[] = []
  const visit = (node: ts.Node) => {
    if (ts.isCallExpression(node)) {
      const call = resolvedCall(node.expression)
      if (call && ['node:fs', 'node:fs/promises'].includes(call.module) && /^(writeFileSync|appendFileSync|mkdirSync|rmSync|rmdirSync|unlinkSync|cpSync|copyFileSync|renameSync|writeFile|appendFile|mkdir|rm|rmdir|unlink|cp|copyFile|rename|createWriteStream)$/.test(call.name)) {
        consumer = node
        const targets = /^(cpSync|copyFileSync|cp|copyFile)$/.test(call.name) ? [node.arguments[1]] : ['rename', 'renameSync'].includes(call.name) ? [node.arguments[0], node.arguments[1]] : [node.arguments[0]]
        const emptyCleanup = (target: ts.Expression | undefined) => {
          if (!['rm', 'rmSync', 'rmdir', 'rmdirSync', 'unlink', 'unlinkSync'].includes(call.name) || !target || !ts.isIdentifier(target)) return false
          const identity = symbol(target), options = identity && values.get(identity)
          return !!options?.length && options.some(value => tempDepth(value) !== undefined) && options.every(value => tempDepth(value) !== undefined || ts.isStringLiteral(value) && value.text === '')
        }
        if (!targets.every(target => tempDepth(target) !== undefined || emptyCleanup(target))) out.push(`${call.name}:${file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1}`)
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return out
}

describe('no test writes into the repository', () => {
  const files = testFiles(SRC)

  it('found the suite it is supposed to be scanning', () => {
    expect(files.length).toBeGreaterThan(100)
    expect(files.some((f) => f.endsWith('consistencyAudit.test.ts'))).toBe(true)
    expect(FS_WRITE.test('writeFileSync' + '(out, data)')).toBe(true)
    expect(FS_WRITE.test('mkdir' + 'Sync(dir)')).toBe(true)
    expect(FS_WRITE.test('const x = readFileSync(p)')).toBe(false)
    expect(FS_WRITE.test('readdirSync(dir)')).toBe(false)
  })

  it('no unlisted test file calls a mutating fs API', () => {
    const offenders = files
      .filter((f) => unsafeWrites(readFileSync(f, 'utf8'), f).length > 0)
      .map((f) => relative(SRC, f).split(/[\\/]/).join('/'))
      .filter((rel) => !(rel in MAY_WRITE))
    expect(offenders).toEqual([])
  })

  it('distinguishes actual temporary writes from repo writes and literal examples', () => {
    const imports = "import { mkdtempSync, writeFileSync as write, rmSync } from 'node:fs'; import { join } from 'node:path'; import { tmpdir } from 'node:os';\n"
    expect(unsafeWrites(imports + "const home = mkdtempSync(join(tmpdir(), 'native-test-')); write(join(home, 'data.json'), 'x'); rmSync(home)")).toEqual([])
    expect(unsafeWrites(imports + "write(join(process.cwd(), 'src/data.json'), 'x')")).toHaveLength(1)
    expect(unsafeWrites(imports + "const home = mkdtempSync(join(tmpdir(), 'native-test-')); write(join(home, '..', 'repo.json'), 'x')")).toHaveLength(1)
    expect(unsafeWrites(imports + "const example = `write(process.cwd(), 'x')`; // write(process.cwd(), 'x')")).toEqual([])
    expect(unsafeWrites("import * as fs from 'node:fs'; fs.writeFileSync('src/data.json', 'x')")).toHaveLength(1)
  })

  it('keeps provenance tied to the actual lexical binding and returned temporary owner', () => {
    const imports = "import { mkdtempSync, writeFileSync as write } from 'node:fs'; import { join } from 'node:path'; import { tmpdir } from 'node:os';\n"
    expect(unsafeWrites(imports + "const home = mkdtempSync(join(tmpdir(), 'native-')); function other() { const home = process.cwd(); write(join(home, 'repo.json'), 'x') }")).toHaveLength(1)
    expect(unsafeWrites(imports + "async function fixture() { const temporary = mkdtempSync(join(tmpdir(), 'native-')); return { temporary }; } async function test() { const { temporary } = await fixture(); write(join(temporary, 'data.json'), 'x') }")).toEqual([])
    expect(unsafeWrites(imports + "async function fixture() { const temporary = process.cwd(); return { temporary }; } async function test() { const { temporary } = await fixture(); write(join(temporary, 'repo.json'), 'x') }")).toHaveLength(1)
    expect(unsafeWrites(imports + "const ready = JSON.parse('{\"repo\":\"/repo\"}'); write(join(ready.repo, 'repo.json'), 'x')")).toHaveLength(1)
    expect(unsafeWrites(imports + "const home = mkdtempSync(join(tmpdir(), 'native-')); const paths = ['safe.json', '../repo.json']; for (const name of paths) write(join(home, name), 'x')")).toHaveLength(1)
  })

  it('does not turn arbitrary JSON or process stdout into temporary-path authority', () => {
    const root = resolve(SRC, '../../..')
    const imports = "import { spawn } from 'node:child_process'; import { writeFileSync } from 'node:fs'; import { resolve } from 'node:path';\n"
    const entry = JSON.stringify(resolve(root, 'checks/runtime/capabilities/workspace/ui_server.py'))
    const cwd = JSON.stringify(root)
    const body = "child.stdout.on('data', chunk => { const ready = JSON.parse(String(chunk)); writeFileSync(resolve(ready.repo, 'data.json'), 'x') })"
    expect(unsafeWrites(imports + `const child = spawn('echo', [${entry}], { cwd: ${cwd} }); ${body}`)).toHaveLength(1)
    expect(unsafeWrites(imports + `const child = spawn('python3', [${entry}], { cwd: ${cwd} }); child.stdout.on('data', chunk => { const ready = JSON.parse('{"repo":"/repo"}'); writeFileSync(resolve(ready.repo, 'data.json'), 'x') })`)).toHaveLength(1)
    expect(unsafeWrites(imports + `const child = spawn('python3', [${entry}], { cwd: ${cwd} }); ${body}`)).toEqual([])
    const contract = nativePathContracts[0], original = contract.sources[contract.entry]
    try {
      contract.sources[contract.entry] = 'unmatched-source-hash'
      expect(unsafeWrites(imports + `const child = spawn('python3', [${entry}], { cwd: ${cwd} }); ${body}`)).toHaveLength(1)
    } finally { contract.sources[contract.entry] = original }
  })

  it('every allowlisted writer is gated so a plain test run cannot fire it', () => {
    for (const [rel, reason] of Object.entries(MAY_WRITE)) {
      expect(reason.trim().length, `${rel} needs a real reason`).toBeGreaterThan(20)
      const source = readFileSync(join(SRC, rel), 'utf8')
      expect(FS_WRITE.test(source), `${rel} no longer writes — delete its row`).toBe(true)
      expect(source.includes(`process.env[WRITE_ENV]`) || source.includes(WRITE_ENV)).toBe(true)
      expect(source).toMatch(/it\.runIf\(/)
    }
  })

  it('the plain reporter test is not the writer any more', () => {
    const source = readFileSync(join(SRC, 'shared/theme/consistencyAudit.test.ts'), 'utf8')
    expect(FS_WRITE.test(source)).toBe(false)
  })
})
