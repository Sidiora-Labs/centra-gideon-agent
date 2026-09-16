import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, sep } from 'node:path'

const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
  const p = join(d, n)
  if (statSync(p).isDirectory()) return walk(p)
  return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
})

// ── Names that shadow a real ui/ primitive ────────────────────────────────────
//
// A local component sharing a name with a shell primitive is one of two things, and the
// difference is the whole judgement:
//
//   COMPOSES the primitive  → fine. A named alias that pins a fixed prop combination is
//                             clearer at the call site than repeating it, and it cannot drift
//                             because the primitive still renders it.
//   REIMPLEMENTS it         → drift. The call site silently loses whatever the primitive
//                             carries (focus/press/hover behaviour, weight, gap, a11y).
//
// 🔴 THE 2026-08-10 CENSUS BELOW WAS HAND-APPLIED AND HAND-KEPT, AND IT LISTED SIX OF EIGHT SITES.
// It concluded "6 shadowed names, 1 real drift". Re-derived mechanically (see the last describe) with
// the rule this file already states — a page file defines a name a `ui/` module EXPORTS — two more
// sites come back, and they failed to be listed for two DIFFERENT reasons:
//
//   FilterMenu (artifacts/ArtifactsSection)  DRIFT, and simply never noticed. A hand-rolled
//     source/collection dropdown beside a sort Segmented — the exact arrangement `ui/FilterMenu`'s
//     own doc says it replaced — while eight other list surfaces rendered the primitive. Converged;
//     that surface gained the active-count badge and inline Clear it never had.
//     🔑 Its local name was `FilterMenu` too, so a grep for the primitive's call sites returned the
//     shadow as a hit. A shadow that shares its primitive's name hides from the search you would use.
//
//   Field (projects/ProjectsSection)         ALREADY JUDGED — as an OPEN OWNER TASTE CALL, but in a
//     different file. `design/rawFormControls.test.tsx` pins that local Field and records the reason:
//     its hint-above-in-sentence-case layout is the owner's to rule on, and swapping it for the shared
//     Field moves 27.9% of the modal's pixels. This cycle converged it, measured the result, and
//     REVERTED — the pin did its job.
//     🔑 So the census was not merely incomplete; its ledger was SPLIT across two files, which is why
//     it could read as complete. The derived list below is keyed by SITE for the same reason: `Field`
//     already carried a verdict (settingsUI's divided settings row), and a name-keyed census lets a
//     second site inherit an unrelated verdict and disappear.
//
// Real tally: 8 shadow sites — 2 fixed drifts, 4 distinctions, 1 composing alias, 1 open taste call.
//
// Census of the six sites found by hand (2026-08-10), and where each landed:
//
//   Button   (settings/UpdatesPanel)          DRIFT — reimplemented. Fixed; see below.
//   Toggle   (tools/ToolsPage)                DISTINCTION — a 3-line alias that renders
//     `<SharedToggle readOnly decorative size="sm" />`. It exists so three tool rows can nest a
//     display-only switch inside a wrapping `<button aria-label>` without a nested interactive
//     or a second unnamed switch in the a11y tree. It COMPOSES the primitive.
//   ContextMenu (files/browse/FileTree)       DISTINCTION — different contract, like ProposalRow.
//     ui/motion/ContextMenu wraps a child and opens at the POINTER on contextmenu/long-press;
//     FileTree's takes explicit {x,y} because the file row opens it from a "⋯" button at a
//     computed anchor. Both clamp to the viewport (the local one says it mirrors the shared
//     clamp, and bug #32 is referenced in the shared one). Converging them means giving the
//     shared component an imperative open-at-coords mode — a new abstraction with one adopter.
//   Spark    (dashboard/widgets/SystemHealth) DISTINCTION — pure name coincidence. ui/Spark is
//     the BRAND MARK (the Gideon, scheme-gradient painted); SystemHealth's is an SVG SPARKLINE
//     over a sample buffer. Nothing shared but five letters.
//   Markdown (ui/content/registerBuiltins)    DISTINCTION — a `lazy()` alias for the
//     MarkdownPreview renderer chunk, local to the registry's naming scheme. Not a component.
//   MonacoEditor (2 files)                    DISTINCTION — both are literally
//     `lazy(() => import('@monaco-editor/react'))`. The "duplicate" is two lazy handles on the
//     same third-party module, which is how code-splitting works; sharing one handle across
//     two routes would defeat the split.
//
// This test locks those verdicts in both directions — it pins the fix AND pins the distinctions,
// so a later "finish the sweep" pass cannot flatten a composing alias or merge two unrelated
// components that share a name.

const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('UpdatesPanel uses the shared Button (was a reimplementation)', () => {
  const src = read('features/settings/UpdatesPanel.tsx')

  it('declares no local Button', () => {
    expect(/function Button\b/.test(src), 'UpdatesPanel should not declare its own Button').toBe(false)
  })

  it('imports the shared Button', () => {
    expect(src).toMatch(/import \{ Button \} from '\.\.\/\.\.\/shared\/ui\/Button'/)
  })

  it('uses the primitive\'s own loading prop rather than an inline spinner', () => {
    expect(src).toMatch(/loading=\{checking\}/)
    expect(src).toMatch(/loading=\{applying\}/)
    expect(/Loader2/.test(src), 'the orphaned Loader2 import should be gone').toBe(false)
  })
})

describe('the five shadowed names that are NOT drift', () => {
  it('ToolsPage Toggle composes the shared Toggle rather than reimplementing it', () => {
    const src = read('features/tools/ToolsPage.tsx')
    expect(src).toMatch(/<SharedToggle[^>]*readOnly[^>]*decorative/)
  })

  it('FileTree ContextMenu takes explicit coords; the shared one wraps a child', () => {
    expect(read('features/files/browse/FileTree.tsx')).toMatch(/function ContextMenu\(\{ x, y, items, onClose \}/)
    expect(read('shared/ui/motion/ContextMenu.tsx')).toMatch(/export function ContextMenu\(\{ items, children, disabled \}/)
  })

  it('the two Sparks are unrelated: a brand mark and a sparkline', () => {
    expect(read('shared/ui/Spark.tsx')).toMatch(/<GideonMark/)
    expect(read('features/dashboard/widgets/SystemHealth.tsx')).toMatch(/function Spark\(\{ samples/)
  })

  it('the MonacoEditor pair are two lazy handles on the same third-party module', () => {
    const lazyMonaco = /lazy\(\(\) => import\('@monaco-editor\/react'\)\)/
    expect(read('shared/ui/content/ContentSurface.tsx')).toMatch(lazyMonaco)
    expect(read('features/knowledge/GistEditor.tsx')).toMatch(lazyMonaco)
  })
})

describe('the census is DERIVED, so a ninth shadow cannot arrive unnoticed', () => {
  const DEF = /(?:^|\n)(?:export )?(?:function ([A-Z]\w+)\(|const ([A-Z]\w+)(?:: [^=]+)? = (?:\(|lazy\(|memo\(|forwardRef))/g
  const EXPORTED = /(?:^|\n)export (?:function ([A-Z]\w+)\(|const ([A-Z]\w+)(?:: [^=]+)? = (?:\(|lazy\(|memo\(|forwardRef))/g

  const files = walk(SRC)
  const namesIn = (subset: string[], re: RegExp) => {
    const found = new Map<string, string[]>()
    for (const abs of subset) {
      for (const m of readFileSync(abs, 'utf8').matchAll(re)) {
        const n = m[1] ?? m[2]
        if (!found.has(n)) found.set(n, [])
        found.get(n)!.push(abs.slice(SRC.length + 1))
      }
    }
    return found
  }
  const uiExports = namesIn(files.filter((f) => f.includes(`${sep}ui${sep}`)), EXPORTED)
  const pageDefs = namesIn(files.filter((f) => !f.includes(`${sep}ui${sep}`)), DEF)

  const VERDICTS: Record<string, 'composes' | 'distinct' | 'fixed' | 'owner-taste-call'> = {
    'Button @ pages/settings/UpdatesPanel.tsx': 'fixed',
    'FilterMenu @ pages/artifacts/ArtifactsSection.tsx': 'fixed',
    'Toggle @ pages/tools/ToolsPage.tsx': 'composes',
    'StatusPill @ pages/settings/bento.tsx': 'composes',
    'ContextMenu @ pages/files/browse/FileTree.tsx': 'distinct',
    'Spark @ pages/dashboard/widgets/SystemHealth.tsx': 'distinct',
    'Field @ pages/settings/settingsUI.tsx': 'distinct',
    'Field @ pages/projects/ProjectsSection.tsx': 'owner-taste-call',
  }

  it('the derived shadow list holds no unjudged name', () => {
    const shadows = [...pageDefs.keys()].filter((n) => uiExports.has(n)).sort()
    expect(uiExports.size, 'the ui/ export scan must resolve').toBeGreaterThan(100)
    expect(shadows.length, 'and some shadows must still be found').toBeGreaterThan(0)

    const sites = shadows.flatMap((n) => pageDefs.get(n)!.map((f) => `${n} @ ${f}`))
    const unjudged = sites.filter((k) => !VERDICTS[k])
    expect(unjudged, `judge these before shipping — compose the primitive, or record why this site is distinct:\n${unjudged.join('\n')}`)
      .toEqual([])
  })

  it('a site recorded as fixed has not come back', () => {
    const live = new Set([...pageDefs.keys()].filter((n) => uiExports.has(n))
      .flatMap((n) => pageDefs.get(n)!.map((f) => `${n} @ ${f}`)))
    const regressed = Object.entries(VERDICTS).filter(([k, v]) => v === 'fixed' && live.has(k)).map(([k]) => k)
    expect(regressed, `these were converged and are shadowed again:\n${regressed.join('\n')}`).toEqual([])
  })

  it('the open taste call is still pinned by the file that owns it', () => {
    const raw = read('shared/theme/rawFormControls.test.tsx')
    expect(raw, "the taste call's own pin must still exist").toMatch(/the local Field is a kept layout, not drift/)
    expect(read('features/projects/ProjectsSection.tsx'), 'and the layout it pins is still there')
      .toMatch(/function Field\(\{ label, hint, children \}/)
  })

  it("ProjectsSection's kept Field still honours the contract it is allowed to keep", () => {
    const src = read('features/projects/ProjectsSection.tsx')
    expect(src).toMatch(/import \{[^}]*\bFieldLabelProvider\b[^}]*\} from '\.\.\/\.\.\/shared\/ui\/forms'/)
    expect(src).toMatch(/<FieldLabelProvider value=\{labelId\}>/)
  })

  it("settingsUI's Field is a settings ROW, which is why it stays", () => {
    const src = read('features/settings/settingsUI.tsx')
    expect(src, 'the divided-row shape is the distinction').toMatch(/border-b border-outline-variant\/30 py-3 last:border-0/)
  })
})
