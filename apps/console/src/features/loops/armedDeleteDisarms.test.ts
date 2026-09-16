import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

const strip = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

const ARMED: { rel: string; setter: string; what: string }[] = [
  { rel: 'features/loops/DesignCockpitPage.tsx', setter: 'setConfirmDelete', what: "the design loop's findings, deliverable and history" },
  { rel: 'features/loops/LoopCockpitPage.tsx', setter: 'setConfirmDelete', what: "the loop's findings, deliverable and history" },
  { rel: 'features/loops/LoopCockpitPage.tsx', setter: 'setConfirmStop', what: 'a run that then cannot resume' },
  { rel: 'features/loops/LoopsListPage.tsx', setter: 'setConfirmDelete', what: 'a loop, from its row' },
  { rel: 'features/chat/SdlcProgressCard.tsx', setter: 'setConfirmDel', what: 'a loop, from the chat card' },
  { rel: 'features/workflows/WorkflowsListPage.tsx', setter: 'setArmed', what: 'a workflow run and its artifacts' },
  { rel: 'features/tasks/formControls.tsx', setter: 'setArmed', what: 'a checklist row the user typed' },
]

function read(rel: string): string {
  return strip(readFileSync(join(SRC, rel), 'utf8'))
}

describe('every armed destroy disarms itself', () => {
  it.each(ARMED)('$rel — $setter (destroys $what)', ({ rel, setter }) => {
    const body = read(rel)
    expect(body, `${rel} must still declare ${setter}`).toContain(`${setter}(`)

    const disarm = new RegExp(
      `setTimeout\\(\\s*\\(\\)\\s*=>\\s*${setter}\\([\\s\\S]{0,80}?\\)\\s*,\\s*4000\\s*\\)`,
    )
    expect(
      disarm.test(body),
      `${rel}: ${setter} arms a destructive control but nothing disarms it after 4000ms — ` +
      'the arm is the only thing making two clicks safer than one, and it must expire',
    ).toBe(true)
  })

  it('the census is real — every listed file exists and is non-trivial', () => {
    for (const { rel } of ARMED) {
      expect(read(rel).length, `${rel} must be substantial`).toBeGreaterThan(500)
    }
    expect(ARMED.length, 'the armed-control census').toBeGreaterThanOrEqual(7)
  })

  it('4000ms is the one interval, so two armed controls never expire differently', () => {
    for (const { rel, setter } of ARMED) {
      const body = read(rel)
      const others = [...body.matchAll(new RegExp(`setTimeout\\(\\s*\\(\\)\\s*=>\\s*${setter}\\([\\s\\S]{0,80}?\\)\\s*,\\s*(\\d+)\\s*\\)`, 'g'))]
        .map((m) => m[1])
        .filter((ms) => ms !== '4000')
      expect(others, `${rel}: ${setter} disarms on a non-standard interval (${others.join(', ')})`).toEqual([])
    }
  })
})

describe('the pattern that has no primitive', () => {
  it('is recorded as hand-rolled, so the count is visible when someone builds one', () => {
    const files = new Set(ARMED.map((a) => a.rel))
    expect(files.size, 'files hand-rolling the armed-destroy pattern').toBeGreaterThanOrEqual(6)
    let hasPrimitive = false
    try {
      readFileSync(join(SRC, 'shared/ui/ArmedButton.tsx'), 'utf8')
      hasPrimitive = true
    } catch { hasPrimitive = false }
    expect(
      hasPrimitive,
      'shared/ui/ArmedButton.tsx now exists — migrate the census above to it and delete this assertion',
    ).toBe(false)
  })
})
