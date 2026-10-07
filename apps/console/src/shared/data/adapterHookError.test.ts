import ts from 'typescript'
import { nodes, sourceFile } from '../testing/sourceOwners'
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })
}

function adapterHooks(): { rel: string; name: string; body: string }[] {
  const out: { rel: string; name: string; body: string }[] = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')
    if (!src.includes('useQuery')) continue
    const imports = sourceFile(src).statements.filter(ts.isImportDeclaration)
    const bindings = imports.flatMap(statement => {
      const named = statement.importClause?.namedBindings
      return ts.isStringLiteral(statement.moduleSpecifier) && /(?:^|\/)data$/.test(statement.moduleSpecifier.text) && named && ts.isNamedImports(named)
        ? named.elements.filter(binding => (binding.propertyName ?? binding.name).text === 'useQuery').map(binding => binding.name.text) : []
    })
    for (const hook of nodes(src, node => ts.isFunctionDeclaration(node) && !!node.name?.text.match(/^use[A-Z]/) && !!node.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.ExportKeyword)) as ts.FunctionDeclaration[]) {
      const body = hook.body?.getText() ?? ''
      if (!nodes(body, node => ts.isCallExpression(node) && bindings.includes(node.expression.getText())).length) continue
      out.push({ rel: abs.slice(SRC.length + 1), name: hook.name!.text, body })
    }
  }
  return out
}

const EXEMPT: Record<string, string> = {
  'app/shell/usePlatform.ts:usePlatform': 'fail-closed by design — "" hides OS-gated UI',
}

describe('an adapter hook re-exposes useQuery\'s error', () => {
  const hooks = adapterHooks()

  it('finds the population — the scan is not vacuous', () => {
    expect(hooks.length, 'exported use* hooks that wrap useQuery').toBeGreaterThanOrEqual(4)
    expect(hooks.map((h) => h.name), 'the canonical one must be in the census').toContain('useAutonomyLadder')
  })

  const carriesError = (body: string): boolean => {
    const queries = new Set<string>()
    const errors = new Set<string>()
    const declarations = nodes(body, ts.isVariableDeclaration) as ts.VariableDeclaration[]
    for (const declaration of declarations) {
      if (!declaration.initializer || !ts.isCallExpression(declaration.initializer) || declaration.initializer.expression.getText() !== 'useQuery') continue
      if (ts.isIdentifier(declaration.name)) queries.add(declaration.name.text)
      if (ts.isObjectBindingPattern(declaration.name)) {
        for (const binding of declaration.name.elements) if ((binding.propertyName ?? binding.name).getText() === 'error') errors.add(binding.name.getText())
      }
    }
    for (let pass = 0; pass < declarations.length; pass++) {
      for (const declaration of declarations) {
        if (!ts.isIdentifier(declaration.name) || !declaration.initializer || !ts.isObjectLiteralExpression(declaration.initializer)) continue
        const props = declaration.initializer.properties
        if (props.some(prop => ts.isSpreadAssignment(prop) && queries.has(prop.expression.getText())) && !props.some(prop => ts.isPropertyAssignment(prop) && prop.name.getText() === 'error')) queries.add(declaration.name.text)
      }
    }
    return (nodes(body, ts.isReturnStatement) as ts.ReturnStatement[]).some(statement => {
      let parent = statement.parent
      while (parent) { if (ts.isFunctionLike(parent)) return false; parent = parent.parent }
      const expression = statement.expression
      if (!expression || !ts.isObjectLiteralExpression(expression)) return false
      return expression.properties.some(prop => {
        const value = ts.isShorthandPropertyAssignment(prop) ? prop.name : ts.isPropertyAssignment(prop) ? prop.initializer : ts.isSpreadAssignment(prop) ? prop.expression : undefined
        if (!value) return false
        if (ts.isIdentifier(value)) return errors.has(value.text) || queries.has(value.text)
        return ts.isPropertyAccessExpression(value) && value.name.text === 'error' && queries.has(value.expression.getText())
      })
    })
  }

  it('every adapter either returns the error or is named as an exemption', () => {
    const swallowing = hooks
      .filter((h) => !carriesError(h.body))
      .map((h) => `${h.rel}:${h.name}`)
      .filter((k) => !(k in EXEMPT))
      .sort()
    expect(swallowing, `these hide a failed read from every consumer:\n${swallowing.join('\n')}`).toEqual([])
  })

  it('the exemptions still exist and still look like their reason', () => {
    for (const key of Object.keys(EXEMPT)) {
      const [rel, name] = key.split(':')
      const hook = hooks.find((h) => h.rel === rel && h.name === name)
      expect(hook, `${key} left the census — prune the exemption`).toBeTruthy()
      expect(carriesError(hook!.body), `${key} now handles the error; drop its exemption`).toBe(false)
    }
  })

  it('the canonical adapter is the shape the others copy', () => {
    const ladder = hooks.find((h) => h.name === 'useAutonomyLadder')!
    expect(ladder.body, 'reads the error from the hook').toMatch(/const \{[^}]*\berror\b[^}]*\} = useQuery/)
    expect(ladder.body, 'and returns it').toMatch(/return \{[\s\S]*\berror\b/)
  })
})
