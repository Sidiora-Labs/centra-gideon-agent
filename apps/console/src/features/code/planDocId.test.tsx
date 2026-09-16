import { describe, expect, it, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { commentStore } from '../files/comments/commentStore'

// global localStorage key (`doc-comments-v1`) shared by every surface that can be commented on:

const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the comment store keys documents by docId alone', () => {
  beforeEach(() => { commentStore.clear() })

  it('two documents with the SAME id are one document — the defect, reproduced', () => {
    commentStore.add({ docId: 'code-plan-requirements', docLabel: 'requirements plan', quote: 'q', comment: 'from project A' })
    const forProjectB = commentStore.all().filter((c) => c.docId === 'code-plan-requirements')
    expect(forProjectB).toHaveLength(1)
    expect(forProjectB[0].comment).toBe('from project A')
  })

  it('project-scoped ids keep two projects apart', () => {
    commentStore.add({ docId: 'code-plan-projA-requirements', docLabel: 'requirements plan', quote: 'q', comment: 'from A' })
    commentStore.add({ docId: 'code-plan-projB-requirements', docLabel: 'requirements plan', quote: 'q', comment: 'from B' })
    const a = commentStore.all().filter((c) => c.docId === 'code-plan-projA-requirements')
    const b = commentStore.all().filter((c) => c.docId === 'code-plan-projB-requirements')
    expect(a).toHaveLength(1)
    expect(b).toHaveLength(1)
    expect(a[0].comment).toBe('from A')
    expect(b[0].comment).toBe('from B')
  })
})

describe('both planning views scope their artifact docId to their own run', () => {
  it('CodePlanningView includes the projectId', () => {
    expect(read('features/code/CodePlanningView.tsx')).toMatch(/docId=\{`code-plan-\$\{projectId\}-\$\{kind\}`\}/)
  })

  it('LoopPlanningView includes the loopId (unchanged, pinned)', () => {
    expect(read('features/loops/LoopPlanningView.tsx')).toMatch(/docId=\{`plan-\$\{loopId\}-\$\{kind\}`\}/)
  })

  it('the Code config is a factory, which is what lets the renderer reach the id', () => {
    const src = read('features/code/CodePlanningView.tsx')
    expect(src).toMatch(/function makeCfg\(projectId: string\): WalkthroughConfig/)
    expect(/^const CFG: WalkthroughConfig/m.test(src), 'CFG should no longer be a module constant').toBe(false)
    expect(src).toMatch(/cfg=\{makeCfg\(projectId\)\}/)
  })

  it('no planning docId is built from the step kind alone', () => {
    for (const rel of ['features/code/CodePlanningView.tsx', 'features/loops/LoopPlanningView.tsx']) {
      const code = read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      const ids = [...code.matchAll(/docId=\{`([^`]+)`\}/g)].map((m) => m[1])
      expect(ids.length, `${rel} should build at least one docId`).toBeGreaterThan(0)
      for (const id of ids) {
        expect(
          /\$\{(projectId|loopId)\}/.test(id),
          `${rel}: docId \`${id}\` has no run scope — two runs would share one comment thread`,
        ).toBe(true)
      }
    }
  })
})
