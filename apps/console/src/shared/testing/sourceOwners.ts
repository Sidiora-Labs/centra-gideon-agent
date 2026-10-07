import ts from 'typescript'

export function sourceFile(source: string) {
  return ts.createSourceFile('consumer.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
}
export function nodes(source: string, predicate: (node: ts.Node) => boolean): ts.Node[] {
  const matches: ts.Node[] = []
  const visit = (node: ts.Node) => { if (predicate(node)) matches.push(node); ts.forEachChild(node, visit) }
  visit(sourceFile(source))
  return matches
}
export function namedOwner(source: string, name: string): string {
  const matches = nodes(source, node => (ts.isFunctionDeclaration(node) && node.name?.text === name) || (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.name.text === name))
  if (matches.length !== 1) throw new Error(`Expected one native owner ${name}, found ${matches.length}`)
  return matches[0].getText()
}
export function apiCalls(source: string, method: string): ts.CallExpression[] {
  return nodes(source, node => ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) && node.expression.expression.getText() === 'api' && node.expression.name.text === method) as ts.CallExpression[]
}
export function callOwner(call: ts.CallExpression): string {
  let node: ts.Node = call
  while (node.parent) {
    if (ts.isFunctionDeclaration(node) || (ts.isVariableDeclaration(node) && node.initializer && (ts.isArrowFunction(node.initializer) || ts.isCallExpression(node.initializer)))) return node.getText()
    node = node.parent
  }
  throw new Error(`No declared owner for ${call.getText()}`)
}
export function callStatement(call: ts.CallExpression): string {
  let node: ts.Node = call
  while (node.parent && !ts.isStatement(node)) node = node.parent
  return node.getText()
}
export function queryRegistration(source: string, key: string): string {
  const matches = nodes(source, node => ts.isCallExpression(node) && node.expression.getText() === 'useQuery' && node.arguments[0]?.getText() === key)
  if (matches.length !== 1) throw new Error(`Expected one query ${key}, found ${matches.length}`)
  return matches[0].getText()
}
