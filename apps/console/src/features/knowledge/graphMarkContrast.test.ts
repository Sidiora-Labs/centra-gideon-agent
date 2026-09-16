import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { SCHEMES } from '../../shared/theme/schemes'
import { weightStroke, weightWidth } from './KnowledgeGraph'


const SRC = readFileSync(join(process.cwd(), "src/features/knowledge/KnowledgeGraph.tsx"), 'utf8')
const TOKENS = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')

function luminance(hex: string): number {
  const h = hex.replace('#', '')
  const chan = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * chan(0) + 0.7152 * chan(2) + 0.0722 * chan(4)
}
function ratio(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}
function over(fg: string, alpha: number, bg: string): string {
  const px = (hex: string, i: number) => parseInt(hex.replace('#', '').slice(i, i + 2), 16)
  const mix = (i: number) => Math.round(px(fg, i) * alpha + px(bg, i) * (1 - alpha))
  return `#${[0, 2, 4].map((i) => mix(i).toString(16).padStart(2, '0')).join('')}`
}

function token(name: string, mode: 'dark' | 'light'): string {
  const all = [...TOKENS.matchAll(new RegExp(`${name}\\s*:\\s*(#[0-9a-fA-F]{6})`, 'g'))].map((m) => m[1])
  expect(all.length, `${name} is declared in tokens.css`).toBeGreaterThanOrEqual(2)
  return mode === 'dark' ? all[0] : all[all.length - 1]
}

const MIN = 3


const NODE_MARKS = ['circle', 'rect', 'ellipse', 'polygon']
const EDGE_MARKS = ['line', 'path', 'polyline']

function blankComments(src: string): string {
  const out = src.split('')
  const wipe = (a: number, b: number) => {
    for (let k = a; k < b && k < src.length; k++) if (out[k] !== '\n') out[k] = ' '
  }
  let i = 0
  while (i < src.length) {
    const c = src[i], d = src[i + 1]
    if (c === '/' && d === '/') { let j = src.indexOf('\n', i); if (j < 0) j = src.length; wipe(i, j); i = j; continue }
    if (c === '/' && d === '*') { let j = src.indexOf('*/', i); j = j < 0 ? src.length : j + 2; wipe(i, j); i = j; continue }
    if (c === '"' || c === "'" || c === '`') {
      let j = i + 1
      while (j < src.length && src[j] !== c) { if (src[j] === '\\') j++; j++ }
      i = j + 1; continue
    }
    i++
  }
  return out.join('')
}

const MARK_TAG = (tag: string) => new RegExp(`<(?:motion\\.)?${tag}[\\s>]`)

function mapBodies(src: string): string[] {
  return [...src.matchAll(/\.map\s*\(/g)].map((m) => balanced(src, m.index + m[0].length - 1))
}

function graphMarkCensus(root: string): string[] {
  const found: string[] = []
  const walk = (dir: string) => {
    let entries
    try { entries = readdirSync(dir, { withFileTypes: true }) } catch { return }
    for (const e of entries) {
      const p = join(dir, e.name)
      if (e.isDirectory()) { walk(p); continue }
      if (!e.name.endsWith('.tsx') || e.name.includes('.test.')) continue
      const bodies = mapBodies(blankComments(readFileSync(p, 'utf8')))
      const perDatum = (tags: string[]) =>
        bodies.some((b) => tags.some((t) => MARK_TAG(t).test(b)))
      if (perDatum(NODE_MARKS) && perDatum(EDGE_MARKS)) found.push(relative(process.cwd(), p))
    }
  }
  walk(root)
  return found.sort()
}

function assertCensusIsReal(census: string[]): void {
  expect(census.length, 'the graph-mark census matched NOTHING — the scan is broken, not the app').toBeGreaterThan(0)
  for (const f of HAND_VERIFIED) {
    expect(census, `${f} renders graph marks and MUST be in the derived census`).toContain(f)
  }
}

const HAND_VERIFIED = [
  'src/features/knowledge/KnowledgeGraph.tsx',
  'src/features/settings/MemoryGraph.tsx',
  'src/features/tasks/DagView.tsx',
]

const OPENERS: Record<string, string> = { '{': '}', '(': ')', '[': ']' }

function balanced(src: string, start: number): string {
  const close = OPENERS[src[start]]
  let depth = 0
  for (let i = start; i < src.length; i++) {
    const c = src[i]
    if (c === '"' || c === "'" || c === '`') {
      const q = c; i++
      while (i < src.length && src[i] !== q) { if (src[i] === '\\') i++; i++ }
      continue
    }
    if (OPENERS[c]) depth++
    else if (c === close || c === '}' || c === ')' || c === ']') { depth--; if (depth === 0) return src.slice(start + 1, i) }
  }
  return src.slice(start + 1)
}

function statementRHS(src: string, from: number): string {
  let depth = 0, i = from
  for (; i < src.length; i++) {
    const c = src[i]
    if (c === '"' || c === "'" || c === '`') {
      const q = c; i++
      while (i < src.length && src[i] !== q) { if (src[i] === '\\') i++; i++ }
      continue
    }
    if (OPENERS[c]) depth++
    else if (c === '}' || c === ')' || c === ']') { if (depth === 0) break; depth-- }
    else if (c === '\n' && depth === 0) {
      const m = /^\s*(\S)/.exec(src.slice(i + 1))
      if (!m || !':?.,+&|='.includes(m[1])) break
    }
  }
  return src.slice(from, i)
}

const TOKEN_REF = /var\((--color-[a-z0-9-]+)\)/g
const RESERVED = new Set(['true', 'false', 'null', 'undefined', 'var'])

function tokensIn(src: string, expr: string, seen = new Set<string>(), depth = 0): Set<string> {
  const out = new Set([...expr.matchAll(TOKEN_REF)].map((m) => m[1]))
  if (depth > 3) return out
  for (const m of expr.matchAll(/\b([A-Za-z_$][A-Za-z0-9_$]*)\b/g)) {
    const id = m[1]
    if (seen.has(id) || RESERVED.has(id)) continue
    seen.add(id)
    const decl = new RegExp(`\\b(?:const|let)\\s+${id}\\b[^=\\n]*=`).exec(src)
    if (!decl) continue
    for (const t of tokensIn(src, statementRHS(src, decl.index + decl[0].length), seen, depth + 1)) out.add(t)
  }
  return out
}

function strokeTokens(file: string): string[] {
  const raw = readFileSync(join(process.cwd(), file), 'utf8')
  const scan = blankComments(raw)
  const out = new Set<string>()
  for (const m of scan.matchAll(/\bstroke\s*=\s*/g)) {
    const j = m.index + m[0].length
    if (raw[j] === '{') { for (const t of tokensIn(raw, balanced(raw, j))) out.add(t) }
    else if (raw[j] === '"' || raw[j] === "'") {
      const end = raw.indexOf(raw[j], j + 1)
      for (const t of raw.slice(j, end).matchAll(TOKEN_REF)) out.add(t[1])
    }
  }
  return [...out].sort()
}

function clearsInBothModes(name: string): boolean {
  return (['dark', 'light'] as const).every((m) => ratio(token(name, m), token('--color-canvas', m)) >= MIN)
}

const VIOLATION_BASELINE: Record<string, string[]> = {
  'src/features/settings/MemoryGraph.tsx': ['--color-outline-variant'],
  'src/features/tasks/DagView.tsx': ['--color-outline-variant'],
}

describe('every file that renders graph marks is under the contrast rail', () => {
  const census = graphMarkCensus(join(process.cwd(), "src"))

  it('derives a non-empty census that recovers the hand-verified files', () => {
    assertCensusIsReal(census)
  })

  it('the vacuity floor FIRES on an empty census — a broken scan cannot read as clean', () => {
    const nothing = graphMarkCensus(join(process.cwd(), "src/__no_such_directory__"))
    expect(nothing, 'a nonexistent root yields no files').toEqual([])
    expect(() => assertCensusIsReal(nothing)).toThrow()
  })

  it('does not enrol files that emit marks without being graphs', () => {
    for (const f of [
      'src/shared/ui/ProgressRing.tsx',
      'src/shared/ui/composer/controls.tsx',
      'src/features/tasks/TaskGraph.tsx',
      'src/shared/ui/content/InfographicView.tsx',
      'src/features/settings/PairingQr.tsx',
    ]) expect(census, `${f} does not render graph marks`).not.toContain(f)
  })

  it('the per-datum condition is LOAD-BEARING — the old signal enrolled the barcode', () => {
    const src = blankComments(
      readFileSync(join(process.cwd(), "src/features/settings/PairingQr.tsx"), 'utf8'),
    )
    const anywhere = (tags: string[]) => tags.some((t) => MARK_TAG(t).test(src))
    expect(anywhere(NODE_MARKS), 'a <rect> plate').toBe(true)
    expect(anywhere(EDGE_MARKS), 'a <path> of modules').toBe(true)
    expect(mapBodies(src), 'and no iteration to hang them on').toEqual([])
    expect(census).not.toContain('src/features/settings/PairingQr.tsx')
  })

  it('reads a boundary token out of every censused file — the per-file scan is not vacuous', () => {
    for (const f of census) {
      expect(strokeTokens(f).length, `${f} paints a mark boundary with at least one token`).toBeGreaterThan(0)
    }
  })

  it('every boundary token is declared in tokens.css, so a typo cannot pass unmeasured', () => {
    for (const f of census) {
      for (const name of strokeTokens(f)) {
        const all = [...TOKENS.matchAll(new RegExp(`${name}\\s*:\\s*(#[0-9a-fA-F]{6})`, 'g'))]
        expect(all.length, `${name} (used in ${f}) is declared for both modes in tokens.css`).toBeGreaterThanOrEqual(2)
      }
    }
  })

  it('mark boundaries clearing 3:1 in both modes — exactly the shrink-only baseline', () => {
    const failing: Record<string, string[]> = {}
    for (const f of census) {
      const bad = strokeTokens(f).filter((t) => !clearsInBothModes(t))
      if (bad.length) failing[f] = bad
    }
    expect(failing, 'a graph mark boundary below 3:1 (SC 1.4.11) — fix the file, then delete its baseline entry')
      .toEqual(VIOLATION_BASELINE)
  })

  it('the rule is satisfiable, and the baseline cannot outlive its files', () => {
    expect(census.filter((f) => strokeTokens(f).every(clearsInBothModes)).length,
      'at least one graph file already clears the bar').toBeGreaterThan(0)
    for (const f of Object.keys(VIOLATION_BASELINE)) {
      expect(census, `${f} has a baseline entry but is no longer censused — delete the entry`).toContain(f)
    }
  })
})

describe('the entity graph marks meet non-text contrast', () => {
  it('tokens.css yields two distinct values per token — the vacuity floor', () => {
    for (const name of ['--color-canvas', '--color-on-surface-low']) {
      const d = token(name, 'dark'), l = token(name, 'light')
      expect(d, `${name} dark`).toMatch(/^#[0-9a-fA-F]{6}$/)
      expect(l, `${name} light`).toMatch(/^#[0-9a-fA-F]{6}$/)
      expect(d, `${name} differs by mode, so the reads are not the same block twice`).not.toBe(l)
    }
  })

  for (const mode of ['dark', 'light'] as const) {
    it(`the resting node outline clears 3:1 in ${mode}`, () => {
      const r = ratio(token('--color-on-surface-low', mode), token('--color-canvas', mode))
      expect(r, `node outline in ${mode}`).toBeGreaterThanOrEqual(MIN)
    })

    it(`the resting relation clears 3:1 in ${mode} at the opacity it ships`, () => {
      const m = /strokeOpacity=\{hover && !active \? 0\.15 : ([\d.]+)\}/.exec(SRC)
      expect(m, 'the resting edge opacity is readable from source').toBeTruthy()
      const alpha = Number(m![1])
      const painted = over(token('--color-on-surface-low', mode), alpha, token('--color-canvas', mode))
      expect(ratio(painted, token('--color-canvas', mode)), `edge at ${alpha} in ${mode}`)
        .toBeGreaterThanOrEqual(MIN)
    })

    it(`the OLD token would still fail in ${mode} — this guard is not vacuous`, () => {
      expect(ratio(token('--color-outline-variant', mode), token('--color-canvas', mode)))
        .toBeLessThan(MIN)
    })
  }

  it('both marks use the neutral, so no scheme can move them', () => {
    expect(SCHEMES.length).toBeGreaterThanOrEqual(11)
    const schemeSrc = readFileSync(join(process.cwd(), "src/shared/theme/schemes.ts"), 'utf8')
    for (const name of ['on-surface-low', 'canvas']) {
      expect(schemeSrc, `${name} is not per-scheme`).not.toMatch(new RegExp(`--color-${name}\\s*:`))
    }
  })

  it('resting marks use the neutral and active marks keep the accent', () => {
    const entityMarks = [...SRC.matchAll(/stroke=\{active \? 'var\(--color-primary\)' : 'var\(--color-on-surface-low\)'\}/g)]
    expect(entityMarks.length, 'the entity mark still names the neutral outright').toBe(1)
    expect(SRC, 'the relation takes its resting colour from the weight ramp')
      .toMatch(/stroke=\{active \? 'var\(--color-primary\)' : weightStroke\(/)
    expect(weightStroke(0), 'a zero-weight relation must resolve to the neutral')
      .toContain('var(--color-on-surface-low)')
    expect(weightStroke(0), 'and must not mix any of the brighter end in at zero')
      .toContain('var(--color-on-surface) 0%')
    expect(SRC, 'the faint outline-variant stroke is gone from both marks')
      .not.toMatch(/: 'var\(--color-outline-variant\)'\}/)
  })

  it('the resting relation is at least a whole pixel wide', () => {
    expect(weightWidth(0, false), 'the lightest resting relation is still a whole pixel')
      .toBeGreaterThanOrEqual(1)
    expect(weightWidth(1, false), 'and the heaviest is wider, not narrower')
      .toBeGreaterThan(weightWidth(0, false))
  })

  it('and that width is what actually PAINTS, at any viewport', () => {
    const marks = [...SRC.matchAll(/vectorEffect="non-scaling-stroke"/g)]
    expect(marks.length, 'one for the relation, one for the entity').toBe(2)
    expect(SRC, 'the relation declares it').toMatch(/<line[^>]*vectorEffect="non-scaling-stroke"/)
    expect(SRC, 'the entity declares it').toMatch(/<circle[^>]*vectorEffect="non-scaling-stroke"/)
  })

  it('the scale that makes it necessary is still what the code assumes', () => {
    expect(SRC, 'a fixed 1000×1000 world space').toMatch(/viewBox=\{`0 0 \$\{W\} \$\{H\}`\}/)
    expect(SRC, 'scaled to fit, which is why the CTM is below 1').toMatch(/preserveAspectRatio="xMidYMid meet"/)
  })
})
