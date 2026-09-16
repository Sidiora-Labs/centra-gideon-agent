import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

function sourceFiles(): string[] {
  const out: string[] = []
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name)
      if (e.isDirectory()) { walk(p); continue }
      if (!/\.tsx$/.test(e.name) || /\.test\.tsx$/.test(e.name)) continue
      out.push(p)
    }
  }
  walk(SRC)
  return out
}

const PRIMITIVES = [
  join('shared/ui', 'Meter.tsx'),
  join('shared/ui', 'ProgressRing.tsx'),
  join('shared/ui', 'WavyProgress.tsx'),
]

const DEFERRED = [
  join('features', 'settings', 'ModelsPanel.tsx'),
  join('features', 'loops', 'RunProgress.tsx'),
  join('shared/ui', 'genui', 'components.tsx'),
]

const TRACK = /\bh-(?:0\.5|1|1\.5|2|2\.5|3|\[\d+px\])\b/
const FILL = /\bh-full\b/

function handRolled(files: string[]): string[] {
  const hits: string[] = []
  for (const f of files) {
    const lines = readFileSync(f, 'utf8').split('\n')
    lines.forEach((ln, i) => {
      const m = ln.match(/className=(?:"([^"]*)"|\{`([^`]*)`\}|\{cx\(([^)]*)\))/)
      const cls = m ? (m[1] ?? m[2] ?? m[3] ?? '') : ''
      if (!cls || !TRACK.test(cls) || !/\bbg-/.test(cls)) return
      const win = lines.slice(i, i + 4).join('\n')
      if (!FILL.test(win)) return
      if (!/width/.test(win)) return
      hits.push(`${f.slice(SRC.length + 1)}:${i + 1}`)
    })
  }
  return hits
}

describe('the determinate progress primitive', () => {
  const files = sourceFiles()

  it('scans a real tree (guards against a silently-empty sweep)', () => {
    expect(files.length).toBeGreaterThan(50)
    const deferredHits = handRolled(files.filter((f) => DEFERRED.some((d) => f.endsWith(d))))
    expect(
      DEFERRED.filter((d) => !deferredHits.some((h) => h.startsWith(d.replace(/\\/g, '/')) || join(SRC, h.split(':')[0]).endsWith(d))),
      'the track+fill detector no longer matches a known hand-rolled bar — the rail has gone vacuous',
    ).toEqual([])
    expect(deferredHits.length).toBeGreaterThanOrEqual(DEFERRED.length)
    expect(DEFERRED.length, 'never add to DEFERRED; adopt ui/Meter instead').toBe(3)
  })

  it('has no hand-rolled determinate bar outside the primitives and the deferred four', () => {
    const scanned = files.filter(
      (f) => !PRIMITIVES.some((p) => f.endsWith(p)) && !DEFERRED.some((d) => f.endsWith(d)),
    )
    const offenders = handRolled(scanned)
    expect(
      offenders,
      'A determinate progress bar must render through `ui/Meter` (or ProgressRing/WavyProgress). ' +
        'A hand-rolled track ships with no role and no aria-valuenow, so axe cannot even see ' +
        'that the progressbar is unnamed — the missing role HIDES the missing name:\n  ' +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('the seven migrated call sites reach for the primitive', () => {
    const adopters: Array<[string, RegExp]> = [
      [join('features', 'ChatPage.tsx'), /label=\{`Uploading \$\{u\.name\}`\}/],
      [join('features', 'ChatPage.tsx'), /label="Prompt budget used by attached knowledge"/],
      [join('features', 'chat', 'WorkflowProgressCard.tsx'), /steps done`\}/],
      [join('features', 'files', 'FilesSection.tsx'), /label=\{`Uploading \$\{u\.name\}`\}/],
      [join('features', 'knowledge', 'KnowledgeCreatePage.tsx'), /label="Upload progress"/],
      [join('features', 'tasks', 'TasksListPage.tsx'), /label=\{`Exit criteria: /],
      [join('shared/ui', 'SystemWidget.tsx'), /label=\{`\$\{label\} usage`\}/],
    ]
    for (const [rel, nameRe] of adopters) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, `${rel} should render <Meter`).toMatch(/<Meter\b/)
      expect(src, `${rel} should name its meter`).toMatch(nameRe)
    }
  })

  it('every Meter call site passes a label', () => {
    const bad: string[] = []
    for (const f of files) {
      const src = readFileSync(f, 'utf8')
      if (!/<Meter\b/.test(src)) continue
      if (/<Meter\b[^>]*\blabel=(?:""|\{''\}|\{``\}|\{undefined\})/s.test(src)) bad.push(f.slice(SRC.length + 1))
    }
    expect(bad, 'a Meter with an empty label is an unnamed progressbar').toEqual([])
  })

  it('no page invents a fill percentage it cannot compute', () => {
    const bad: string[] = []
    for (const f of files) {
      if (PRIMITIVES.some((p) => f.endsWith(p))) continue
      const lines = readFileSync(f, 'utf8').split('\n')
      lines.forEach((ln, i) => {
        if (!/\bh-full\b/.test(ln) && !/\bwidth:/.test(ln)) return
        const win = lines.slice(Math.max(0, i - 1), i + 2).join('\n')
        if (!/\bh-full\b/.test(win) || !/\bwidth:/.test(win)) return
        for (const lit of win.match(/['"](\d+(?:\.\d+)?)%['"]/g) ?? []) {
          const n = Number(lit.replace(/['"%]/g, ''))
          if (n > 0 && n < 100) { bad.push(`${f.slice(SRC.length + 1)}:${i + 1} → ${lit}`); break }
        }
      })
    }
    expect(
      bad,
      'A determinate fill pinned to a literal percentage invents progress it does not have. ' +
        'Render the indeterminate WavyProgress, or nothing:\n  ' + bad.join('\n  '),
    ).toEqual([])
  })
})
