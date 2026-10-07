import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { jsxTags } from '../testing/jsxContracts'
import { controlAvailability } from './controlState'


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

const BUSY =
  /busy|saving|loading|pending|submitting|acting|probing|searching|testing|running|reloading|installing|deleting|syncing|creating|sending|launching|retrying|regenning|bundling|starting|stopping|pulling|applying|resolving|refreshing|rebuilding|generating|uploading|downloading|reconnecting|scanning|verifying|repairing|restoring|pausing|resuming|cancelling|dismissing|promoting|consolidating|rechecking|exporting|importing|merging|pinPending|flash/i
const REASON = /disabledReason|unavailableWhen|aria-disabled|title=|hint=/i

type Site = { key: string; rel: string; line: number }

const siteKey = (rel: string, expr: string) => `${rel}  disabled={${expr}}`

function unexplained(): Site[] {
  const out: Site[] = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')
    for (const site of jsxTags(src)) {
      const gate = site.attributes.get('disabled')
      if (!gate?.startsWith('{')) continue
      const expr = gate.slice(1, -1).trim()
      const ids = (expr.match(/[A-Za-z_$][\w$]*/g) ?? []).filter((x) => !['true', 'false', 'null', 'undefined', 'length'].includes(x))
      if (!ids.length || BUSY.test(expr)) continue
      if (REASON.test(site.element)) continue
      const rel = abs.replace(SRC + '/', '')
      out.push({ key: siteKey(rel, expr), rel, line: site.line })
    }
  }
  return out.sort((a, b) => a.key.localeCompare(b.key) || a.line - b.line)
}

const CLASSIFIED: Record<string, { n?: number; why: string }> = {
  'features/inbox/InboxSettingsPanel.tsx  disabled={sourcesOn === null}': { why: 'loading (sourcesOn === null)' },
  'features/inbox/InboxSettingsPanel.tsx  disabled={engagementOn === null}': { why: 'loading (engagementOn === null)' },
  'features/settings/InboxSettingsPanel.tsx  disabled={sourcesOn === null}': { why: 'loading (sourcesOn === null)' },
  'features/settings/InboxSettingsPanel.tsx  disabled={engagementOn === null}': { why: 'loading (engagementOn === null)' },
  'features/knowledge/KnowledgeListPage.tsx  disabled={!o.item_id}': { why: "the reason is the button's own label text" },
  'features/tasks/TaskDetail.tsx  disabled={readOnly}': { n: 2, why: 'read-only task; the panel states it once with a lock' },
  'features/tasks/TaskDetail.tsx  disabled={!onOpenTask}': { why: 'no navigation handler → informational row (cursor-default)' },
  'features/tasks/TaskDetail.tsx  disabled={!dep || !onOpenTask}': { why: 'no navigation handler → informational row (cursor-default)' },
  'features/tasks/TaskForm.tsx  disabled={!projectId}': { why: 'depends on the Project field rendered immediately above' },
  'shared/ui/widget/WidgetFrame.tsx  disabled={pinned}': { why: "the reason is the button's own name, which flips to \"Pinned to dashboard\"" },
}


const PASSTHROUGH = /^(ui\/(Slider|Segmented|HeaderActions|forms)\.tsx|pages\/(loops\/DesignCockpitPage|settings\/(DurabilityPanel|ProjectionRulesPanel|SecurityPanel))\.tsx):/

describe('the disabled-reason census', () => {
  it('finds the population — the scan is not vacuous', () => {
    const all = walk(SRC).flatMap((abs) => [...readFileSync(abs, 'utf8').matchAll(/disabled=\{/g)])
    expect(all.length, 'conditional disabled props').toBeGreaterThanOrEqual(300)
  })

  it('every unexplained control is classified or a pass-through', () => {
    const found = unexplained().filter((s) => !PASSTHROUGH.test(`${s.rel}:`))
    const where = (k: string) =>
      found.filter((s) => s.key === k).map((s) => `${s.rel}:${s.line}`).join(', ')

    const surprises = [...new Set(found.map((s) => s.key))].filter((k) => !(k in CLASSIFIED))
    expect(
      surprises.map((k) => `${k}   at ${where(k)}`),
      'a control disabled for a reason nobody states',
    ).toEqual([])

    const actual: Record<string, number> = {}
    for (const s of found) actual[s.key] = (actual[s.key] ?? 0) + 1
    const expected = Object.fromEntries(Object.entries(CLASSIFIED).map(([k, v]) => [k, v.n ?? 1]))
    expect(actual, 'the classification must match the remainder exactly, count included').toEqual(expected)
  })

  it('each classified site still has the shape it is excused for', () => {
    const code = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

    expect(code('shared/ui/Button.tsx')).toMatch(/activateControl\(event, state.blocked, onClick\)/)
    expect(controlAvailability(true, false, 'Choose a project').nativeDisabled).toBe(false)

    expect(code('features/knowledge/KnowledgeListPage.tsx'),
      'and its label really does explain the state').toMatch(/\(removed — insight kept\)/)

    expect(code('features/tasks/TaskDetail.tsx'),
      'the cursor says "not a button", not "blocked"').toMatch(/disabled:cursor-default/)
    expect(code('features/tasks/TaskDetail.tsx'),
      'and the read-only state is stated once for the section').toMatch(/read-only|readOnly/)

    const pin = jsxTags(code('shared/ui/widget/WidgetFrame.tsx'), ['SquareIconButton']).find(site => site.attributes.get('disabled') === '{artifact.pinned}')
    expect(pin?.attributes.get('loading')).toBe('{artifact.pinPending}')
    expect(code('shared/ui/widget/WidgetFrame.tsx'),
      'and the name really does state the gate').toMatch(/artifact\.pinned \? 'Pinned to dashboard'/)

    expect(code('features/tasks/TaskForm.tsx'),
      'and the Project field it depends on is right above').toMatch(/<Field label="Project">/)

    for (const rel of ['features/settings/ProjectionRulesPanel.tsx', 'features/settings/DurabilityPanel.tsx']) {
      expect(code(rel), `${rel} takes disabled from a caller`)
        .toMatch(/disabled\??:\s*boolean|disabled\s*\}/)
    }
  })

  it('the keys carry no line numbers — the property this change exists to establish', () => {
    const positional = Object.keys(CLASSIFIED).filter((k) => /:\d+/.test(k))
    expect(positional, 'a classified key must identify a control, not a position').toEqual([])
    expect(Object.keys(CLASSIFIED).every((k) => k.includes('disabled={')),
      'every key names the expression it excuses').toBe(true)
  })
})
