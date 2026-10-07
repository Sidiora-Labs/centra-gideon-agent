import ts from 'typescript'

export function jsxTags(source: string, names?: readonly string[]) {
  const file = ts.createSourceFile('contract.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const out: { name: string; tag: string; element: string; line: number; attributes: Map<string, string> }[] = []
  const visit = (node: ts.Node) => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const name = node.tagName.getText(file)
      if (!names || names.includes(name)) {
        const attributes = new Map<string, string>()
        for (const attribute of node.attributes.properties) {
          if (ts.isJsxAttribute(attribute)) attributes.set(attribute.name.getText(file), attribute.initializer?.getText(file) ?? '')
        }
        out.push({ name, tag: node.getText(file), element: ts.isJsxOpeningElement(node) ? node.parent.getText(file) : node.getText(file), line: file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1, attributes })
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return out
}

// Count only a literal array mapped directly to a hinted JSX template.
export function literalHintInstances(source: string, name: string): number {
  const file = ts.createSourceFile('contract.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const arrays = new Map<string, ts.ArrayLiteralExpression>()
  const discover = (node: ts.Node) => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      let value = node.initializer
      while (ts.isAsExpression(value) || ts.isParenthesizedExpression(value)) value = value.expression
      if (ts.isArrayLiteralExpression(value)) arrays.set(node.name.text, value)
    }
    ts.forEachChild(node, discover)
  }
  discover(file)
  let count = 0
  const visit = (node: ts.Node) => {
    if ((ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) && node.tagName.getText(file) === name) {
      const hint = node.attributes.properties.find(attribute => ts.isJsxAttribute(attribute) && attribute.name.getText(file) === 'hint')
      if (hint && ts.isJsxAttribute(hint) && hint.initializer && ts.isJsxExpression(hint.initializer) && hint.initializer.expression) {
        const value = hint.initializer.expression
        let ancestor: ts.Node | undefined = node.parent
        while (ancestor && !ts.isCallExpression(ancestor)) ancestor = ancestor.parent
        if (ancestor && ts.isCallExpression(ancestor) && ts.isPropertyAccessExpression(ancestor.expression) && ancestor.expression.name.text === 'map' && ts.isIdentifier(ancestor.expression.expression)) {
          const callback = ancestor.arguments[0]
          const array = arrays.get(ancestor.expression.expression.text)
          if (array && callback && ts.isArrowFunction(callback) && callback.parameters[0] && ts.isIdentifier(callback.parameters[0].name) && ts.isPropertyAccessExpression(value) && value.expression.getText(file) === callback.parameters[0].name.text && value.name.text === 'hint') {
            count += array.elements.filter(entry => ts.isObjectLiteralExpression(entry) && entry.properties.some(property => ts.isPropertyAssignment(property) && property.name.getText(file) === 'hint' && ts.isStringLiteral(property.initializer) && property.initializer.text.trim().length > 0)).length
            ts.forEachChild(node, visit)
            return
          }
        }
      }
      if (hint) count++
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return count
}
