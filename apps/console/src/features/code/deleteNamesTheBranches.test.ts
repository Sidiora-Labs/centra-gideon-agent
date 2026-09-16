import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { codeDeleteBody } from './codeMeta'

const WEB = process.cwd()
const REPO = join(WEB, '../..')
const read = (rel: string) => readFileSync(join(WEB, 'src', rel), 'utf8')
const py = (rel: string) => readFileSync(join(REPO, 'runtime/gideon', rel), 'utf8')
const strip = (s: string) => s
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/^(\s*)\/\/.*$/gm, '$1')

const bound = codeDeleteBody({ name: 'api', status: 'ready', workspace_dir: '/repo' })
const green = codeDeleteBody({ name: 'api', status: 'ready' })
const running = codeDeleteBody({ name: 'api', status: 'running', workspace_dir: '/repo' })

describe('the bound-workspace body tells the truth about the repo', () => {
  it('🔴 it no longer claims blanket safety', () => {
    expect(bound, 'the blanket form must be gone')
      .not.toMatch(/files are left untouched\.\s*$/)
  })

  it('it names the branch deletion, and that the branches are Gideon’s own', () => {
    expect(bound).toMatch(/gideon\/task-\*/)
    expect(bound, 'force-deleted, not merely removed').toMatch(/force-deleted/)
  })

  it('🔑 it distinguishes unmerged from merged — the difference decides whether to cancel', () => {
    expect(bound, 'the loss is scoped to unmerged commits').toMatch(/not yet merged is lost/)
    expect(bound, 'and merged work is stated safe').toMatch(/merged is safe/)
  })

  it('🪤 the TRUE half of the original clause survives', () => {
    expect(bound).toMatch(/workspace folder and the files in it are left untouched/)
  })
})

describe('the task deletion is named, on every branch of the copy', () => {
  it('all three variants say the tasks go', () => {
    for (const [label, body] of [['bound', bound], ['greenfield', green], ['running', running]] as const) {
      expect(body, `${label}: tasks are named`).toMatch(/every task under it are permanently deleted/)
    }
  })

  it('the greenfield managed-folder warning is unchanged — it was always accurate', () => {
    expect(green).toMatch(/keeps its files in its own managed folder/)
    expect(green).toMatch(/Move anything you want to keep out first/)
    expect(green).not.toMatch(/left untouched/)
  })

  it('the running variant still says the worker stops', () => {
    expect(running).toMatch(/still working — deleting it stops the worker/)
  })
})

describe('one owner: the sentence cannot drift back into two copies', () => {
  it('both call sites delegate, and neither builds a body inline', () => {
    for (const rel of ['features/code/CodeSection.tsx', 'features/code/CodeCockpitPage.tsx']) {
      const code = strip(read(rel))
      expect(code, `${rel} delegates`).toMatch(/body: codeDeleteBody\(p\)/)
      expect(code, `${rel} must not hand-roll the body again`)
        .not.toMatch(/left untouched/)
    }
  })
})

describe('VACUITY: the destruction this copy warns about is real', () => {
  it('the teardown is armed by the SAME predicate the copy branches on', () => {
    const mgr = py('automation/loop/manager.py')
    expect(mgr, 'armed on a bound workspace').toMatch(
      /if loop is not None and \(loop\.workspace_dir or ""\)\.strip\(\):/,
    )
    expect(mgr, 'and it calls the worktree sweep').toMatch(/worktree\.cleanup_all\(/)
  })

  it('the sweep really force-deletes branches, with the user’s workspace as cwd', () => {
    const wt = py('automation/loop/worktree.py')
    expect(wt, 'worktree removal is forced').toMatch(/"worktree", "remove", "--force"/)
    expect(wt, 'and the branch is force-deleted').toMatch(/"branch", "-D"/)
  })

  it('🔑 but the worktrees live OUTSIDE the workspace — which is why the kept clause is true', () => {
    expect(py('automation/loop/worktree.py'), 'rooted under config_dir(), not the workspace')
      .toMatch(/under\s*\n?\s*``config_dir\(\)``, NOT under the workspace itself/)
  })

  it('the tasks really are deleted on delete — and only on delete', () => {
    const mgr = py('automation/loop/manager.py')
    expect(mgr, 'delete tears down tasks').toMatch(/teardown_tasks\(/)
    expect(mgr, 'stop/complete/fail keep them').toMatch(/WITHOUT touching its Tasks/)
  })

  it('🔑 the Stop dialog on the same page already said this, which is the sharpest evidence', () => {
    expect(strip(read('features/code/CodeCockpitPage.tsx')), 'Stop names the worktree + branch loss')
      .toMatch(/loses its own worktree and branch/)
  })
})
