import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const read = (rel: string) => readFileSync(join(process.cwd(), "src", rel), 'utf8')
const strip = (s: string) => s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

const LIST = strip(read('features/tasks/TasksListPage.tsx'))
const DAG = strip(read('features/tasks/TaskGraph.tsx'))
const WIDGET = strip(read('features/dashboard/widgets/TasksWidget.tsx'))

describe('every place a task is listed hands over its full title', () => {
  it('the list row', () => {
    expect(LIST).toMatch(/block truncate text-\[0\.9375rem\][\s\S]{0,120}title=\{t\.title\}>\{t\.title\}<\/span>/)
  })

  it('the DAG node', () => {
    expect(DAG).toMatch(/truncate text-\[0\.8125rem\] leading-tight[\s\S]{0,160}title=\{t\.title\}>\{t\.title\}<\/div>/)
  })

  it('the dashboard widget row', () => {
    expect(WIDGET).toMatch(/truncate text-on-surface" title=\{t\.title\}>\{t\.title\}<\/span>/)
  })

  it('each title attribute is the rendered value, not a paraphrase', () => {
    for (const [name, src] of [['list', LIST], ['dag', DAG], ['widget', WIDGET]] as const) {
      const m = /title=\{t\.title\}>\{t\.title\}</.exec(src)
      expect(m, `${name}: title and text are one expression`).toBeTruthy()
    }
  })

  it('all three still truncate — the fix is recovery, not re-layout', () => {
    expect(LIST).toMatch(/block truncate/)
    expect(DAG).toMatch(/truncate text-\[0\.8125rem\]/)
    expect(WIDGET).toMatch(/truncate text-on-surface/)
  })

  it('the row name still carries the title AND the status — the half that already worked', () => {
    expect(LIST).toMatch(/<RowHitTarget label=\{`\$\{t\.title\} — \$\{sm\.label\}`\} \/>/)
  })

  it('the shared SidePanel title has since been fixed on its own terms', () => {
    const panel = strip(read('shared/ui/SidePanel.tsx'))
    expect(panel, 'the shared panel title still exists').toMatch(/data-type="title-l" className="text-on-surface truncate/)
    expect(panel, 'and now carries a type-guarded title of its own')
      .toMatch(/title=\{typeof title === 'string' \? title : undefined\}/)
  })
})
