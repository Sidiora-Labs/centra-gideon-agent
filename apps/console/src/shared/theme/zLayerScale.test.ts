import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'


const SRC = join(process.cwd(), "src")
const TOKENS_CSS = join(SRC, 'shared/theme/tokens.css')
const EXEMPT_DIRS = ['shared/theme/']

const NUMERIC_Z = /\bz-\[-?\d/

interface Baseline { maxArbitraryZLayers: number; files: string[] }

function loadBaseline(): Baseline {
  const j = JSON.parse(readFileSync(join(SRC, 'shared/theme/zLayerScale.baseline.json'), 'utf8'))
  return { maxArbitraryZLayers: j.maxArbitraryZLayers, files: j.files }
}

function walk(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    const rel = relative(SRC, p).replace(/\\/g, '/')
    if (EXEMPT_DIRS.some((d) => rel.startsWith(d))) continue
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

function isCommentLine(trimmed: string): boolean {
  return trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')
}

function scanNumericZ(): { byFile: Record<string, number>; total: number } {
  const byFile: Record<string, number> = {}
  let total = 0
  for (const f of walk(SRC)) {
    const rel = relative(SRC, f).replace(/\\/g, '/')
    const text = readFileSync(f, 'utf8')
    let n = 0
    for (const line of text.split('\n')) {
      if (isCommentLine(line.trim())) continue
      n += (line.match(new RegExp(NUMERIC_Z, 'g')) ?? []).length
    }
    if (n) { byFile[rel] = n; total += n }
  }
  return { byFile, total }
}

function resolveScale(): Record<'content' | 'overlay' | 'modal' | 'menu' | 'toast', number> {
  const css = readFileSync(TOKENS_CSS, 'utf8')
  const content = Number(/--z-content:\s*(\d+)/.exec(css)?.[1])
  const rung = (name: string): number => {
    const m = new RegExp(`--z-${name}:\\s*calc\\(\\s*var\\(--z-content\\)\\s*\\+\\s*(\\d+)\\s*\\)`).exec(css)
    return content + Number(m?.[1])
  }
  return { content, overlay: rung('overlay'), modal: rung('modal'), menu: rung('menu'), toast: rung('toast') }
}

describe('z-layer scale is a well-ordered ladder (CD-05)', () => {
  const z = resolveScale()

  it('defines all five rungs as finite numbers', () => {
    for (const [name, v] of Object.entries(z)) {
      expect(Number.isFinite(v), `--z-${name} must resolve to a number (found ${v})`).toBe(true)
    }
  })

  it('orders content < overlay < modal < menu < toast', () => {
    expect(z.content).toBeLessThan(z.overlay)
    expect(z.overlay).toBeLessThan(z.modal)
    expect(z.modal).toBeLessThan(z.menu)
    expect(z.menu).toBeLessThan(z.toast)
  })

  it('keeps control-anchored menus BELOW toasts and the command palette — the CD-05 payload', () => {
    expect(
      z.menu,
      `--z-menu (${z.menu}) must sit below --z-toast (${z.toast}) or menus paint over toasts/palette again`,
    ).toBeLessThan(z.toast)
  })
})

describe('no bespoke numeric z-[N] outside the shrinking baseline', () => {
  const baseline = loadBaseline()
  const { byFile, total } = scanNumericZ()
  const offenders = Object.keys(byFile).sort()

  it('every file carrying a numeric z-[N] is on the baseline (a NEW one turns CI red)', () => {
    const unlisted = offenders.filter((f) => !baseline.files.includes(f))
    expect(
      unlisted,
      `Bespoke numeric z-[N] found in file(s) not on the baseline:\n  ${unlisted.join('\n  ')}\n` +
        `Add a rung to the --z-* scale (design/tokens.css) and use z-[var(--z-*)] instead. ` +
        `If this is a deliberate exception, add the file to zLayerScale.baseline.json.`,
    ).toEqual([])
  })

  it(`total numeric z-[N] count must not exceed the baseline (${baseline.maxArbitraryZLayers})`, () => {
    expect(
      total,
      `Numeric z-[N] count rose to ${total} (baseline ${baseline.maxArbitraryZLayers}). ` +
        `Route the new layer through the --z-* scale, or — if migrating DOWN — lower ` +
        `maxArbitraryZLayers in zLayerScale.baseline.json in the same commit.`,
    ).toBeLessThanOrEqual(baseline.maxArbitraryZLayers)
  })

  it('the scanner is not vacuously green — the regex still recognizes the violation shapes', () => {
    for (const bad of ['z-[200]', 'z-[9999]', '-z-[10]', 'z-[60]']) {
      expect(NUMERIC_Z.test(bad), `NUMERIC_Z must match "${bad}"`).toBe(true)
    }
    for (const good of ['z-[var(--z-toast)]', 'z-[var(--z-modal)]', 'z-[calc(var(--z-content)+1)]']) {
      expect(NUMERIC_Z.test(good), `NUMERIC_Z must NOT match "${good}"`).toBe(false)
    }
  })
})


const FIXED_STANDARD_Z = /(['"`])((?:(?!\1)[\s\S]){0,400}?)\1/g

function scanStandardScaleFixed(): { byFile: Record<string, number>; total: number } {
  const byFile: Record<string, number> = {}
  let total = 0
  for (const f of walk(SRC)) {
    const rel = relative(SRC, f).replace(/\\/g, '/')
    const text = readFileSync(f, 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
      .replace(/(^|[^:"'`])\/\/[^\n]*/g, (m, p1) => p1 + ' '.repeat(m.length - p1.length))
    let n = 0
    for (const m of text.matchAll(FIXED_STANDARD_Z)) {
      const lit = m[2]
      if (/\bfixed\b/.test(lit) && /(?:^|[\s'"`])z-\d+\b/.test(lit)) n++
    }
    if (n) { byFile[rel] = n; total += n }
  }
  return { byFile, total }
}

describe('the standard-scale spelling is ratcheted too', () => {
  const z = resolveScale()
  const baseline = JSON.parse(readFileSync(join(SRC, 'shared/theme/zLayerScale.baseline.json'), 'utf8'))
  const { byFile, total } = scanStandardScaleFixed()

  it('the two control-anchored menu primitives ride --z-menu, ABOVE dialogs', () => {
    const pop = readFileSync(join(SRC, 'shared/ui/Popover.tsx'), 'utf8')
    expect(pop, 'the portaled popover must ride the menu rung').toMatch(/fixed z-\[var\(--z-menu\)\]/)
    const ctx = readFileSync(join(SRC, 'shared/ui/motion/ContextMenu.tsx'), 'utf8')
    expect(ctx, 'the context menu must ride the menu rung').toMatch(/fixed z-\[var\(--z-menu\)\]/)
    for (const [name, src] of [['Popover', pop], ['ContextMenu', ctx]] as const) {
      expect(src, `${name} must not go back to a bare z-50 while fixed`).not.toMatch(/fixed z-50\b/)
    }
  })

  it('--z-menu really is above --z-modal, which is what makes that fix correct', () => {
    expect(z.menu, 'a menu must out-paint a dialog').toBeGreaterThan(z.modal)
  })

  it("the tree's THIRD control-anchored menu rides --z-menu too", () => {
    const tree = readFileSync(join(SRC, 'features/files/browse/FileTree.tsx'), 'utf8')
    expect(tree, 'the file-tree context menu must ride the menu rung')
      .toMatch(/fixed z-\[var\(--z-menu\)\]/)
    expect(tree, 'and must not go back to a bare z-50 while fixed').not.toMatch(/fixed z-50\b/)
  })

  it('an overlay that DECLARES role=dialog rides --z-modal, not the content ceiling', () => {
    const rail = readFileSync(join(SRC, 'shared/ui/NavRail.tsx'), 'utf8')
    expect(rail, 'the drawer must ride the modal rung').toMatch(/fixed left-0 top-0 z-\[var\(--z-modal\)\]/)
    expect(rail, "the scrim must track the drawer's rung, not a hardcoded neighbour")
      .toMatch(/fixed inset-0 z-\[calc\(var\(--z-modal\)-1\)\]/)
  })

  it('the full-screen content takeovers ride --z-content by name', () => {
    const named: Array<[string, RegExp]> = [
      ['features/chat/ChatFilePanel.tsx', /fixed inset-0 z-\[var\(--z-content\)\] flex flex-col bg-surface/],
      ['shared/ui/SidePanel.tsx', /fixed inset-0 z-\[var\(--z-content\)\] flex flex-col bg-surface/],
      ['features/knowledge/KnowledgeDetail.tsx', /fixed inset-0 z-\[var\(--z-content\)\] flex flex-col/],
      ['shared/ui/widget/WidgetFrame.tsx', /fixed inset-4 z-\[var\(--z-content\)\]/],
      ['shared/ui/widget/ReactWidgetFrame.tsx', /fixed inset-4 z-\[var\(--z-content\)\]/],
    ]
    for (const [rel, re] of named) {
      expect(readFileSync(join(SRC, rel), 'utf8'), `${rel} must name the content rung`).toMatch(re)
    }
  })

  it('every remaining standard-scale entry is exempt for a reason that still HOLDS', () => {
    // global rung would be the wrong vocabulary for), and two are positioned beneath the content
    const reasons: Record<string, RegExp> = baseline.standardScaleFixedFiles.length
      ? {
          'shared/ui/DegradedChip.tsx': /absolute right-0 z-50/,
          'shared/ui/FeedbackThumbs.tsx': /absolute right-0 top-7 z-50/,
          'shared/ui/content/ContentSurface.tsx': /absolute right-0 z-50/,
          'features/tasks/TasksListPage.tsx': /fixed inset-x-0 bottom-6 z-30/,
          'features/terminal/TerminalDrawer.tsx': /fixed inset-x-0 bottom-0 z-40/,
        }
      : {}
    expect(Object.keys(reasons).sort(), 'every baselined file needs a checked reason, and vice versa')
      .toEqual([...baseline.standardScaleFixedFiles].sort())
    for (const [rel, proof] of Object.entries(reasons)) {
      expect(readFileSync(join(SRC, rel), 'utf8'), `${rel}: its exemption reason must still hold`)
        .toMatch(proof)
    }
    for (const rel of Object.keys(reasons)) {
      expect(baseline._perFile[rel], `${rel}: the baseline must state the reason in words too`)
        .toBeTruthy()
    }
  })

  it('no NEW file puts a fixed overlay on the standard scale', () => {
    const unlisted = Object.keys(byFile).sort().filter((f) => !baseline.standardScaleFixedFiles.includes(f))
    expect(
      unlisted,
      'A `fixed` overlay carrying a standard-scale z-<n> in a file not on the baseline:\n  '
      + unlisted.join('\n  ')
      + '\nRoute it through the --z-* scale (design/tokens.css) with z-[var(--z-*)].',
    ).toEqual([])
  })

  it('the standard-scale count only shrinks', () => {
    expect(
      total,
      `fixed+standard-scale z count rose to ${total} (baseline ${baseline.maxStandardScaleFixed}). `
      + 'Use z-[var(--z-*)], or lower maxStandardScaleFixed in the same commit when migrating DOWN.',
    ).toBeLessThanOrEqual(baseline.maxStandardScaleFixed)
  })

  it('the scanner finds the population it is filtering (not vacuously green)', () => {
    expect(total, 'the class-literal scan must still find the baselined overlays').toBeGreaterThanOrEqual(3)
  })
})
