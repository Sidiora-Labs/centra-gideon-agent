import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { runLook, nodeLook } from './workflowMeta'


const WORKFLOW_META = join(process.cwd(), "src/features/workflows/workflowMeta.ts")
const LOOP_META = join(process.cwd(), "src/shared/data/loopStatus.ts")
const TASK_META = join(process.cwd(), "src/features/tasks/taskMeta.tsx")

describe('a finished run is "Completed" everywhere', () => {
  it('workflowMeta says Completed', () => {
    expect(runLook('complete').label).toBe('Completed')
  })

  it('the three status registries agree on the word', () => {
    expect(readFileSync(WORKFLOW_META, 'utf8')).toMatch(/complete: \{ label: 'Completed'/)
    expect(readFileSync(LOOP_META, 'utf8')).toMatch(/complete: \{ label: 'Completed'/)
    expect(readFileSync(TASK_META, 'utf8')).toMatch(/key: 'done', label: 'Completed'/)
  })

  it('no status registry still ships the bare adjective', () => {
    for (const f of [WORKFLOW_META, LOOP_META]) {
      expect(readFileSync(f, 'utf8'), `${f} should not label a status 'Complete'`)
        .not.toMatch(/label: 'Complete'[,}]/)
    }
  })

  it('keeps everything else about the status look intact', () => {
    const look = runLook('complete')
    expect(look.tone).toBe('text-success')
    expect(look.spin).toBeUndefined()
    expect(look.icon).toBeTruthy()
  })
})

describe('the deliberate non-conversions', () => {
  it('a NODE stays "Done" — a step, not a run', () => {
    expect(nodeLook('done').label).toBe('Done')
  })

  it('the filter chips keep their bucket wording', () => {
    const chips: Array<[string, RegExp]> = [
      ['src/features/code/CodeSection.tsx', /key: 'done', label: 'Done'/],
      ['src/features/inbox/InboxPage.tsx', /key: 'handled', label: 'Done'/],
      ['src/features/loops/LoopsListPage.tsx', /key: 'done', label: 'Done'/],
    ]
    for (const [rel, re] of chips) {
      expect(readFileSync(join(process.cwd(), rel), 'utf8'), `${rel} filter chip should stay "Done"`)
        .toMatch(re)
    }
  })

  it('the task row ACTION stays the verb "Complete"', () => {
    const src = readFileSync(join(process.cwd(), "src/features/tasks/TasksListPage.tsx"), 'utf8')
    expect(src).toMatch(/label: 'Complete', onSelect: onComplete/)
  })
})
