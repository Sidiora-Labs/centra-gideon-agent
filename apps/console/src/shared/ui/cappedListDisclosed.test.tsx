import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { MoreRow } from './MoreRow'


const SRC = join(process.cwd(), "src")
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

const CAP = /([A-Za-z_$][\w$.?!\[\]]*)\s*\.slice\(\s*0\s*,\s*(\d+)\s*\)\s*\.map\(/g

function cappedLists() {
  const out: Array<{ rel: string; base: string; root: string; cap: number; totalStated: boolean; after: string }> = []
  for (const abs of walk(SRC)) {
    const src = strip(readFileSync(abs, 'utf8'))
    const lines = src.split('\n')
    for (const m of src.matchAll(CAP)) {
      const ln = src.slice(0, m.index!).split('\n').length - 1
      const root = m[1].split('[')[0].split('.')[0].replace(/[!?]/g, '')
      const before = lines.slice(Math.max(0, ln - 8), ln + 1).join('\n')
      const totalStated = new RegExp(
        `(?:label|title)=\\{?[\`"'][^\`"']*\\$\\{[^}]*${root}[\\w$.?!\\[\\]]*\\.length`,
      ).test(before)
      out.push({
        rel: abs.replace(SRC + '/', ''), base: m[1], root, cap: Number(m[2]), totalStated,
        after: lines.slice(ln, ln + 24).join('\n'),
      })
    }
  }
  return out
}

function rowFor(list: { root: string; after: string }): string | null {
  for (const m of list.after.matchAll(/<MoreRow[\s\S]{0,140}?\/>/g)) {
    if (new RegExp(`total=\\{[^}]*\\b${list.root}\\b`).test(m[0])) return m[0]
  }
  return null
}

describe('MoreRow', () => {
  it('says nothing when nothing is hidden', () => {
    const { container } = render(<MoreRow total={6} shown={6} />)
    expect(container.textContent, 'a full list gets no residue line').toBe('')
    expect(render(<MoreRow total={2} shown={8} />).container.textContent,
      'and a list shorter than its own cap cannot hide anything').toBe('')
  })

  it('states the shown and total counts', () => {
    render(<MoreRow total={47} shown={30} />)
    expect(screen.getByText('Showing 30 of 47')).toBeTruthy()
  })

  it('names what is hidden when the caller says what it is', () => {
    render(<MoreRow total={247} shown={200} noun="rows" />)
    expect(screen.getByText('Showing 200 of 247 rows')).toBeTruthy()
  })

  it('stays subject-less where the list above it already says what these are', () => {
    const { container } = render(<MoreRow total={9} shown={6} />)
    expect(container.textContent?.trim(), 'no dangling noun, no guessed one').toBe('Showing 6 of 9')
  })

  it('is one wording, so eleven sites cannot drift again', () => {
    const src = readFileSync(join(SRC, 'shared/ui/MoreRow.tsx'), 'utf8')
    expect((src.match(/Showing/g) ?? []).length, 'exactly one place spells it').toBe(1)
  })
})

describe('every list whose label states a total discloses its cap', () => {
  it('the census finds them, and none is silent', () => {
    const lists = cappedLists()
    expect(lists.length, 'the sweep must find the capped lists').toBeGreaterThanOrEqual(25)
    const stated = lists.filter((l) => l.totalStated)
    expect(stated.length, 'and the label-states-a-total subset').toBeGreaterThanOrEqual(8)
    const silent = stated.filter((l) => !rowFor(l)).map((l) => `${l.rel} (${l.base})`)
    expect(silent, 'a label that promises N above a list of fewer owes the difference').toEqual([])
  })

  it('every MoreRow `shown` equals the cap actually applied', () => {
    let checked = 0
    for (const l of cappedLists()) {
      const row = rowFor(l)
      const m = row?.match(/shown=\{(\d+)\}/)
      if (!m) continue
      checked++
      expect(Number(m[1]), `${l.rel}: the ${l.root} row's shown must match .slice(0, ${l.cap})`).toBe(l.cap)
    }
    expect(checked, 'the scan must actually pair some of them').toBeGreaterThanOrEqual(9)
  })

  it('no site spells the sentence for itself any more', () => {
    const adhoc: string[] = []
    for (const abs of walk(SRC)) {
      if (abs.endsWith('MoreRow.tsx')) continue
      const src = strip(readFileSync(abs, 'utf8'))
      for (const line of src.split('\n')) {
        const at = line.search(/\.length\s*-\s*\d+\}?\s*more/)
        if (at < 0) continue
        const insideTemplate = line.slice(0, at).includes('`')
        if (insideTemplate) continue
        adhoc.push(`${abs.replace(SRC + '/', '')}: ${line.trim().slice(0, 70)}`)
      }
    }
    expect(adhoc, 'three spellings of one sentence is how it drifted the first time').toEqual([])
  })

  it('an EXPANDING control is not a residue line — left alone deliberately', () => {
    const src = strip(readFileSync(join(SRC, 'features/code/CodeCockpitPage.tsx'), 'utf8'))
    expect(src, 'still an interactive disclosure').toMatch(/title=\{`Show \$\{hidden\} more file\$\{[^}]*\}`\}>\+\{hidden\} more<\/TextLink>/)
    expect(src, 'and it still expands the list').toMatch(/onClick=\{\(\) => setExpanded\(true\)\}/)
  })

  it('a truncated TABLE names what it dropped', () => {
    const pins: Array<[string, RegExp]> = [
      ['features/chat/toolRenderers/primitives.tsx', /<MoreRow total=\{body\.length\} shown=\{200\} noun="rows"/],
      ['features/files/browse/FilePreviews.tsx', /<MoreRow total=\{body\.length\} shown=\{500\} noun="rows"/],
      ['features/tools/ToolOutput.tsx', /<MoreRow total=\{cols\.length\} shown=\{8\} noun="columns"/],
    ]
    for (const [rel, re] of pins) {
      expect(strip(readFileSync(join(SRC, rel), 'utf8')), `${rel} must disclose its cap`).toMatch(re)
    }
  })

  it('a residue under a table is NAMED, because "… 6 more" would read as rows', () => {
    for (const rel of ['features/chat/toolRenderers/primitives.tsx', 'features/files/browse/FilePreviews.tsx',
      'features/tools/ToolOutput.tsx']) {
      const src = strip(readFileSync(join(SRC, rel), 'utf8'))
      for (const tag of src.match(/<MoreRow[\s\S]{0,160}?\/>/g) ?? []) {
        const underTable = /noun=/.test(tag) || !/entries\.length/.test(tag)
        if (!underTable) continue
        expect(tag, `${rel}: a table residue needs its noun`).toMatch(/noun="(rows|columns)"/)
      }
    }
  })

  it('the row sits OUTSIDE the table — a div in a tbody is invalid markup', () => {
    for (const rel of ['features/chat/toolRenderers/primitives.tsx', 'features/files/browse/FilePreviews.tsx',
      'features/tools/ToolOutput.tsx']) {
      const src = strip(readFileSync(join(SRC, rel), 'utf8'))
      expect(src, `${rel}: the residue must follow </table>`).toMatch(/<\/table>[\s\S]{0,320}?<MoreRow/)
      for (const body of src.match(/<tbody>[\s\S]*?<\/tbody>/g) ?? []) {
        expect(body, `${rel}: a div inside tbody is invalid markup`).not.toContain('<MoreRow')
      }
    }
  })

  it('a dashboard widget preview says it is one', () => {
    const widgets = join(SRC, 'features/dashboard/widgets')
    const caps = cappedLists().filter((l) => l.rel.startsWith('features/dashboard/widgets/'))
    expect(caps.length, 'the widget caps must be found').toBeGreaterThanOrEqual(5)
    const pins: Array<[string, RegExp]> = [
      ['features/dashboard/widgets/TasksWidget.tsx', /<MoreRow total=\{visible\.length\} shown=\{6\} \/>/],
      ['features/dashboard/widgets/ScheduleWidget.tsx', /<MoreRow total=\{visible\.length\} shown=\{6\} \/>/],
      ['features/dashboard/widgets/PinnedArtifacts.tsx', /<MoreRow total=\{resolved\.length\} shown=\{6\} \/>/],
      ['features/dashboard/widgets/OnThisMachine.tsx', /<MoreRow total=\{rows\.length\} shown=\{5\} \/>/],
    ]
    for (const [rel, re] of pins) {
      expect(strip(readFileSync(join(SRC, rel), 'utf8')), `${rel} must disclose its cap`).toMatch(re)
    }
    expect(readdirSync(widgets).length, 'the widget directory must be readable').toBeGreaterThan(4)
  })

  it('the capped frontend census covers indirect and named caps', () => {
    const pins: Array<[string, RegExp]> = [
      ['shared/ui/NotificationBell.tsx', /<MoreRow total=\{items\.length\} shown=\{MAX_SHADE\} noun="notifications"/],
      ['features/dashboard/widgets/DesktopLiveView.tsx', /<MoreRow total=\{\(data\.feed \?\? \[\]\)\.length\} shown=\{FEED_ROWS\} noun="events"/],
      ['features/projects/ProjectsSection.tsx', /<MoreRow total=\{items\.length\} shown=\{8\} noun="items"/],
      ['features/settings/MemoryGraph.tsx', /<MoreRow total=\{groups\.length\} shown=\{8\} noun="groups"/],
      ['features/settings/DoctorPanel.tsx', /<MoreRow total=\{snap\.recent_runs\.length\} shown=\{5\} noun="runs"/],
      ['features/ChatPage.tsx', /<MoreRow total=\{matching\.length\} shown=\{40\} noun="artifacts"/],
    ]
    for (const [rel, re] of pins) {
      expect(strip(readFileSync(join(SRC, rel), 'utf8')), `${rel} must state shown N of total`).toMatch(re)
    }
  })

  it('the SCHEDULE widget applied a standard it already held', () => {
    const src = strip(readFileSync(join(SRC, 'features/dashboard/widgets/ScheduleWidget.tsx'), 'utf8'))
    expect(src, 'the fold disclosure it already had').toMatch(/\$\{scheduleSuppressed\} suppressed by a gate/)
    expect(src, 'and the cap disclosure it was missing').toMatch(/<MoreRow total=\{visible\.length\}/)
    expect(src, 'the residue is measured against what the fold shows').not.toMatch(
      /<MoreRow total=\{schedule\.length\}/,
    )
  })

  it('a GENERATED feed is deliberately left silent', () => {
    const src = strip(readFileSync(join(SRC, 'features/dashboard/widgets/Suggestions.tsx'), 'utf8'))
    expect(src, 'still capped — the count is owned by suggestionCapMatchesProducer.test.ts').toMatch(
      /items\.slice\(0, \d+\)/,
    )
    expect(src, 'and still silent, on purpose').not.toMatch(/MoreRow/)
    expect(src, 'its own copy says it is a feed, which is why').toMatch(/they build from your activity/)
  })

  it('a string SUMMARY keeps its own grammar', () => {
    const src = strip(readFileSync(join(SRC, 'features/ChatPage.tsx'), 'utf8'))
    expect(src, 'the tool-name summary still discloses its own residue').toMatch(
      /toolNames\.slice\(0, 3\)\.join\(', '\)[\s\S]{0,60}toolNames\.length - 3\} more/,
    )
  })
})
