import ts from 'typescript'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const src = (file: string) => readFileSync(join(import.meta.dirname, file), 'utf8')

describe('per-turn overview census', () => {
  it('keeps Session Map as the sole per-turn overview', () => {
    const activityPanel = src('ChatActivityPanel.tsx')
    const chatPage = src('../ChatPage.tsx')

    expect(activityPanel).not.toMatch(/act-(?:tab|panel)-index|label:\s*['"]Index['"]|activity\.index/)
    expect(chatPage).toContain('<SessionMarkerRail turns={turns}')
    const owners: string[] = []
    const tree = ts.createSourceFile('ChatPage.tsx', chatPage, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    const visit = (node: ts.Node) => {
      if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
        if (node.attributes.properties.some((attribute) => ts.isJsxAttribute(attribute) && attribute.name.getText(tree) === 'onJumpTo')) {
          owners.push(node.tagName.getText(tree))
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(tree)
    expect(owners).toEqual(['SessionMarkerRail'])
    expect(src('SessionWorkspace.tsx')).not.toContain('onJumpTo=')
  })
})
