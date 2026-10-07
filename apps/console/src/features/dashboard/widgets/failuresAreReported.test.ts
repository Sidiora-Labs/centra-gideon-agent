import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import ts from 'typescript'


const read = (rel: string) => {
  const src = readFileSync(join(process.cwd(), rel), 'utf8')
  return src.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
}

describe('ActionCenter actions report their failures (#324)', () => {
  const CODE = read('src/features/dashboard/widgets/ActionCenter.tsx')

  it('routes every action through the shared reporter, keeping no local copy', () => {
    expect(CODE).toMatch(/import \{ reportingWrite \} from '\.\.\/\.\.\/\.\.\/app\/shell\/reportingWrite'/)
    expect([...CODE.matchAll(/(function|const)\s+reportingWrite\b\s*[=(]/g)].length).toBe(0)
    expect(CODE).toMatch(/await reportingWrite\(what, fn\)/)
  })

  it('keeps no empty catch on the action path, and only marks done on success', () => {
    expect(CODE).not.toMatch(/catch\s*\{\s*\}/)
    expect(CODE).toMatch(/if \(ok\) setDone/)
  })

  it('every withBusy caller supplies a human sentence for the toast', () => {
    const calls = [...CODE.matchAll(/withBusy\(e\.key, `[^`]+`/g)]
    expect(calls.length).toBeGreaterThanOrEqual(5)
  })
})

describe('bulk task ops read the per-item outcomes (#478)', () => {
  const page = read('src/features/tasks/TasksListPage.tsx')
  const hook = ts.createSourceFile('taskCollectionState.ts', read('src/features/tasks/taskCollectionState.ts'), ts.ScriptTarget.Latest, true)
  function matching(root: ts.Node, predicate: (node: ts.Node) => boolean): ts.Node[] {
    const nodes: ts.Node[] = []
    const visit = (node: ts.Node) => { if (predicate(node)) nodes.push(node); ts.forEachChild(node, visit) }
    visit(root)
    return nodes
  }
  const bulk = matching(hook, node => ts.isVariableDeclaration(node) && node.name.getText(hook) === 'runBulk')[0]
  const isCall = (node: ts.Node, name: string) => ts.isCallExpression(node) && node.expression.getText(hook) === name

  it('reads `failed` from the 200 body instead of discarding the response', () => {
    expect(page).toContain('useTaskCollection(')
    expect(page).toContain("runBulk('update', { status: 'done' })")
    expect(page).toContain("runBulk('delete')")
    expect(bulk).toBeDefined()
    const assignments = matching(bulk, node => ts.isVariableDeclaration(node) && !!node.initializer && ts.isAwaitExpression(node.initializer) && isCall(node.initializer.expression, 'api.tasksBulk'))
    expect(assignments).toHaveLength(1)
    const assignment = assignments[0] as ts.VariableDeclaration
    const result = assignment.name.getText(hook)
    const failures = matching(bulk, node => ts.isIfStatement(node) && ts.isBinaryExpression(node.expression) && node.expression.left.getText(hook) === `${result}.failed` && node.expression.operatorToken.kind === ts.SyntaxKind.GreaterThanToken && node.expression.right.getText(hook) === '0')
    expect(failures).toHaveLength(1)
    const refusal = failures[0] as ts.IfStatement
    const notifications = matching(refusal.thenStatement, node => isCall(node, 'notify'))
    expect(notifications).toHaveLength(1)
    expect(notifications[0].getText(hook)).toContain(`${result}.total`)
    expect(notifications[0].getText(hook)).toContain(`${result}.errors`)
    expect(notifications[0].getText(hook)).toContain("'error'")
  })

  it('reports transport failures through the shared funnel, not an empty catch', () => {
    expect(bulk).toBeDefined()
    const catches = matching(bulk, ts.isCatchClause) as ts.CatchClause[]
    expect(catches).toHaveLength(1)
    expect(catches[0].block.statements.length).toBeGreaterThan(0)
    const reports = matching(catches[0], node => ts.isCallExpression(node) && ts.isCallExpression(node.expression) && node.expression.expression.getText(hook) === 'reportActionFailure') as ts.CallExpression[]
    expect(reports).toHaveLength(1)
    expect(reports[0].arguments[0].getText(hook)).toBe(catches[0].variableDeclaration?.name.getText(hook))
    expect(hook.text).toContain("import { reportActionFailure } from '../../app/shell/reportingWrite'")
    const finallyBlocks = matching(bulk, node => ts.isTryStatement(node) && !!node.finallyBlock) as ts.TryStatement[]
    expect(finallyBlocks).toHaveLength(1)
    expect(finallyBlocks[0].finallyBlock?.getText(hook)).toContain('bulkLock.current = false')
    expect(matching(finallyBlocks[0].finallyBlock!, node => isCall(node, 'load'))).toHaveLength(1)
  })
})

describe('WorkflowProgressCard only vanishes on a real 404 (#549)', () => {
  const CODE = read('src/features/chat/WorkflowProgressCard.tsx')

  it('branches the catch on ApiError.status — 404 collapses, anything else keeps the card', () => {
    expect(CODE).toMatch(/e instanceof ApiError && e\.status === 404\) setGone\(true\)/)
    expect(CODE).toMatch(/setLoadFailed\(true\)/)
  })

  it('a never-loaded card with a failed read renders a retry line, not null and not a skeleton', () => {
    expect(CODE).toMatch(/!vm && loadFailed/)
    expect(CODE).toMatch(/Try again/)
  })

  it('a successful load clears the failure mark so recovery is visible', () => {
    expect(CODE).toMatch(/setVm\(foldSnapshot\(snap\)\); setLoadFailed\(false\)/)
  })
})
