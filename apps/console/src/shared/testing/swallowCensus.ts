import ts from 'typescript'

function unwrap(node: ts.Expression): ts.Expression {
  while (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isTypeAssertionExpression(node) || ts.isSatisfiesExpression(node)) node = node.expression
  return node
}

function substitute(expression: ts.Expression): boolean {
  const node = unwrap(expression)
  if (node.kind === ts.SyntaxKind.NullKeyword || node.kind === ts.SyntaxKind.UndefinedKeyword) return true
  if (ts.isIdentifier(node) && node.text === 'undefined') return true
  if (ts.isArrayLiteralExpression(node) || ts.isObjectLiteralExpression(node)) return true
  if (ts.isStringLiteral(node) && node.text === '') return true
  if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && /^set[A-Z]/.test(node.expression.text)) {
    return node.arguments.length === 1 && substitute(node.arguments[0])
  }
  return false
}

export function swallowedReads(source: string): Array<{ line: number; text: string }> {
  const file = ts.createSourceFile('surface.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const matches: Array<{ line: number; text: string }> = []
  const visit = (node: ts.Node) => {
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) && node.expression.name.text === 'catch') {
      const callback = node.arguments[0]
      if (callback && (ts.isArrowFunction(callback) || ts.isFunctionExpression(callback)) && callback.parameters.length === 0) {
        const body = callback.body
        const swallowed = ts.isBlock(body)
          ? body.statements.length === 0 || (body.statements.length === 1 && ts.isReturnStatement(body.statements[0]) && (!body.statements[0].expression || substitute(body.statements[0].expression)))
          : substitute(body)
        if (swallowed) matches.push({ line: file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1, text: node.getText(file) })
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return matches
}
