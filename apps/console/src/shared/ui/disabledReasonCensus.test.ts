import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


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

function elementWithChildren(src: string, i: number): string {
  const a = src.lastIndexOf('<', i)
  if (a < 0) return ''
  const tag = /^<([A-Za-z][\w.]*)/.exec(src.slice(a))?.[1]
  if (!tag) return src.slice(a, a + 400)
  let depth = 0
  let j = a
  for (; j < src.length; j++) {
    const c = src[j]
    if (c === '{') depth++
    else if (c === '}') depth--
    else if (c === '>' && depth === 0) break
  }
  if (src[j - 1] === '/') return src.slice(a, j + 1)
  const close = `</${tag}>`
  const openRe = new RegExp(`<${tag}[\\s/>]`, 'g')
  let k = j + 1
  let level = 1
  while (k < src.length && level > 0) {
    const nextClose = src.indexOf(close, k)
    openRe.lastIndex = k
    const nextOpen = openRe.exec(src)
    if (nextClose < 0) break
    if (nextOpen && nextOpen.index < nextClose) { level++; k = nextOpen.index + nextOpen[0].length }
    else { level--; k = nextClose + close.length }
  }
  return src.slice(a, k)
}

type Site = { key: string; rel: string; line: number }

const siteKey = (rel: string, expr: string) => `${rel}  disabled={${expr}}`

const codeOf = (s: string): string => s
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/(^|[^:])\/\/[^\n]*/g, (m, p) => p + ' '.repeat(m.length - p.length))

function unexplained(): Site[] {
  const out: Site[] = []
  for (const abs of walk(SRC)) {
    const src = codeOf(readFileSync(abs, 'utf8'))
    for (const m of src.matchAll(/disabled=\{([^}]{1,90})\}/g)) {
      const expr = m[1].trim()
      const ids = (expr.match(/[A-Za-z_$][\w$]*/g) ?? []).filter((x) => !['true', 'false', 'null', 'undefined', 'length'].includes(x))
      if (!ids.length || BUSY.test(expr)) continue
      if (REASON.test(elementWithChildren(src, m.index!))) continue
      const rel = abs.replace(SRC + '/', '')
      out.push({ key: siteKey(rel, expr), rel, line: src.slice(0, m.index!).split('\n').length })
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

    expect(code('shared/ui/Button.tsx'), 'the carrier really implements soft-off').toMatch(/softOff/)

    expect(code('features/knowledge/KnowledgeListPage.tsx'),
      'and its label really does explain the state').toMatch(/\(removed — insight kept\)/)

    expect(code('features/tasks/TaskDetail.tsx'),
      'the cursor says "not a button", not "blocked"').toMatch(/disabled:cursor-default/)
    expect(code('features/tasks/TaskDetail.tsx'),
      'and the read-only state is stated once for the section').toMatch(/read-only|readOnly/)

    expect(code('shared/ui/widget/WidgetFrame.tsx'),
      'pinned gates the press; pinPending stays the in-flight prop').toMatch(/disabled=\{pinned\}\s+loading=\{pinPending\}/)
    expect(code('shared/ui/widget/WidgetFrame.tsx'),
      'and the name really does state the gate').toMatch(/pinned \? 'Pinned to dashboard'/)

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
