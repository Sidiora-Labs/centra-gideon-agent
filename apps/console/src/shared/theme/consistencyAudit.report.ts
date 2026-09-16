
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, relative, resolve } from 'node:path'

export type DriftCategory =
  | 'color'
  | 'spacing'
  | 'radius'
  | 'shadow'
  | 'duration'

export type PrimitiveDrift =
  | 'raw-button'
  | 'raw-input'
  | 'raw-dialog'

export interface DriftHit {
  file: string
  line: number
  category: DriftCategory
  snippet: string
}

export interface PrimitiveHit {
  file: string
  kind: PrimitiveDrift
  count: number
}

export interface AuditResult {
  drift: DriftHit[]
  byFile: Record<string, Record<DriftCategory, number>>
  byCategory: Record<DriftCategory, number>
  primitives: PrimitiveHit[]
  primitivesByFile: Record<string, Record<PrimitiveDrift, number>>
  totals: { driftHits: number; filesScanned: number; filesWithDrift: number }
}

const SRC = join(process.cwd(), "src")

const EXEMPT_DIRS = ['shared/theme/']
const COLOR_EXEMPT_FILES = new Set<string>([
  'shared/ui/DotGlow.tsx',
  'shared/ui/GideonMark.tsx',
  'shared/ui/Spark.tsx',
  'shared/ui/WavyProgress.tsx',
  'features/files/fileMeta.ts',
  'shared/ui/content/registerBuiltins.ts',
  'shared/ui/content/exporters.ts',
  'features/terminal/TerminalView.tsx',
  'features/code/DiffReveal.tsx',
  'features/code/TypingReveal.tsx',
  'app/shell/appearance.tsx',
  'features/settings/settingsWidgets.tsx',
])

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

const HEX = /#[0-9a-fA-F]{3,8}\b/
const RGB_HSL = /\b(rgba?|hsla?)\(/
const RAW_PX = /style=\{\{[^}]*?\b\d+px\b/
const PX_OK_CONTEXT = /minmax\(|repeat\(|\bmin\(|\bmax\(|\bclamp\(|\b(border|outline)(-[a-z]+)?:\s*[^;}]*\d+px|border[A-Z][a-zA-Z]*:\s*[`'"]?\s*\$?\{?[^}]*\d+px|Math\.(min|max)\(/
const BORDER_RADIUS = /borderRadius:\s*[`'"]?\s*\d+px/
const BOX_SHADOW = /boxShadow:\s*[`'"][^`'"]*\d+px[^`'"]*[`'"]/
const DURATION = /transition[^:]*:\s*[^;}]*\b\d+ms|animation[^:]*:\s*[^;}]*\b\d+ms|duration-\[\d+ms\]/

function isCommentLine(trimmed: string): boolean {
  return trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')
}

function scanFile(file: string, rel: string): DriftHit[] {
  const text = readFileSync(file, 'utf8')
  const hits: DriftHit[] = []
  const colorExempt = COLOR_EXEMPT_FILES.has(rel)
  text.split('\n').forEach((line, i) => {
    const trimmed = line.trim()
    if (isCommentLine(trimmed)) return
    const at = (category: DriftCategory) =>
      hits.push({ file: rel, line: i + 1, category, snippet: trimmed.slice(0, 100) })

    if (!colorExempt && (HEX.test(line) || RGB_HSL.test(line))) at('color')
    if (BORDER_RADIUS.test(line)) at('radius')
    else if (RAW_PX.test(line) && !/calc\([^)]*var\(/.test(line) && !PX_OK_CONTEXT.test(line)) at('spacing')
    if (BOX_SHADOW.test(line) && !/var\(--/.test(line)) at('shadow')
    if (DURATION.test(line) && !/var\(--/.test(line)) at('duration')
  })
  return hits
}

const CATEGORIES: DriftCategory[] = ['color', 'spacing', 'radius', 'shadow', 'duration']

function scanPrimitives(file: string, rel: string): PrimitiveHit[] {
  if (rel.startsWith('shared/ui/')) return []
  const text = readFileSync(file, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:"'`])\/\/[^\n]*/g, (m, p1) => p1 + ' '.repeat(m.length - p1.length))
  const count = (re: RegExp) => (text.match(re) ?? []).length
  const out: PrimitiveHit[] = []
  const btn = count(/<button[\s>]/g)
  const inp = count(/<(input|textarea|select)[\s>]/g)
  const dlg = count(/role=["']dialog["']|<dialog[\s>]/g)
  if (btn) out.push({ file: rel, kind: 'raw-button', count: btn })
  if (inp) out.push({ file: rel, kind: 'raw-input', count: inp })
  if (dlg) out.push({ file: rel, kind: 'raw-dialog', count: dlg })
  return out
}

export function scanDrift(): AuditResult {
  const files = walk(SRC)
  const drift: DriftHit[] = []
  const primitives: PrimitiveHit[] = []
  for (const f of files) {
    const rel = relative(SRC, f).replace(/\\/g, '/')
    drift.push(...scanFile(f, rel))
    primitives.push(...scanPrimitives(f, rel))
  }

  const byFile: Record<string, Record<DriftCategory, number>> = {}
  const byCategory = Object.fromEntries(CATEGORIES.map((c) => [c, 0])) as Record<DriftCategory, number>
  for (const h of drift) {
    byFile[h.file] ??= Object.fromEntries(CATEGORIES.map((c) => [c, 0])) as Record<DriftCategory, number>
    byFile[h.file][h.category]++
    byCategory[h.category]++
  }

  const primitivesByFile: Record<string, Record<PrimitiveDrift, number>> = {}
  for (const p of primitives) {
    primitivesByFile[p.file] ??= { 'raw-button': 0, 'raw-input': 0, 'raw-dialog': 0 }
    primitivesByFile[p.file][p.kind] += p.count
  }

  return {
    drift,
    byFile,
    byCategory,
    primitives,
    primitivesByFile,
    totals: {
      driftHits: drift.length,
      filesScanned: files.length,
      filesWithDrift: Object.keys(byFile).length,
    },
  }
}


export interface A11yCoverage {
  outlineNoneFiles: string[]
  outlineNoneCount: number
  localFocusVisibleFiles: string[]
  reducedMotionFiles: string[]
  animatedFiles: number
  hasGlobalReducedMotion: boolean
  hasGlobalFocusRing: boolean
}

const OUTLINE_NONE = /outline-none|outline:\s*none|outline:\s*0\b/
const FOCUS_VISIBLE = /focus-visible|:focus-visible/
const REDUCED_MOTION = /prefers-reduced-motion/
const ANIMATED = /transition-|animate-|animation:|@keyframes/

function walkAll(dir: string, exts: RegExp): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...walkAll(p, exts))
    else if (exts.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

export function scanA11y(): A11yCoverage {
  const files = walkAll(SRC, /\.(tsx?|css)$/)
  const outlineNoneFiles: string[] = []
  const localFocusVisibleFiles: string[] = []
  const reducedMotionFiles: string[] = []
  let outlineNoneCount = 0
  let animatedFiles = 0
  let hasGlobalReducedMotion = false
  let hasGlobalFocusRing = false

  for (const f of files) {
    const rel = relative(SRC, f).replace(/\\/g, '/')
    const text = readFileSync(f, 'utf8')
    const onMatches = text.match(new RegExp(OUTLINE_NONE, 'g'))
    if (onMatches && !rel.startsWith('shared/theme/')) {
      outlineNoneFiles.push(rel)
      outlineNoneCount += onMatches.length
    }
    if (FOCUS_VISIBLE.test(text) && !rel.startsWith('shared/theme/')) localFocusVisibleFiles.push(rel)
    if (REDUCED_MOTION.test(text) && !rel.startsWith('shared/theme/')) reducedMotionFiles.push(rel)
    if (ANIMATED.test(text)) animatedFiles++
    if (rel === 'shared/theme/tokens.css') {
      hasGlobalReducedMotion = /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[^}]*\*/.test(text)
      hasGlobalFocusRing = /:focus-visible\s*\{[^}]*outline/.test(text)
    }
  }

  return {
    outlineNoneFiles,
    outlineNoneCount,
    localFocusVisibleFiles,
    reducedMotionFiles,
    animatedFiles,
    hasGlobalReducedMotion,
    hasGlobalFocusRing,
  }
}

export function rankFiles(res: AuditResult): { file: string; score: number; detail: string }[] {
  const scores = new Map<string, number>()
  const bump = (file: string, n: number) => scores.set(file, (scores.get(file) ?? 0) + n)
  for (const h of res.drift) {
    const w = h.category === 'color' ? 5 : h.category === 'shadow' ? 3 : 2
    bump(h.file, w)
  }
  for (const p of res.primitives) {
    const w = p.kind === 'raw-dialog' ? 4 : p.kind === 'raw-button' ? 2 : 1
    bump(p.file, p.count * w)
  }
  return [...scores.entries()]
    .map(([file, score]) => {
      const d = res.byFile[file]
      const pf = res.primitivesByFile[file]
      const parts: string[] = []
      if (d) for (const c of CATEGORIES) if (d[c]) parts.push(`${d[c]} ${c}`)
      if (pf) {
        if (pf['raw-button']) parts.push(`${pf['raw-button']} raw-button`)
        if (pf['raw-input']) parts.push(`${pf['raw-input']} raw-input`)
        if (pf['raw-dialog']) parts.push(`${pf['raw-dialog']} raw-dialog`)
      }
      return { file, score, detail: parts.join(', ') }
    })
    .sort((a, b) => b.score - a.score)
}

export function countInlineFontWeights(): { total: number; byFile: Record<string, number> } {
  const files = walk(SRC)
  const re = /fontVariationSettings:\s*[`'"]\s*"wght"/
  const byFile: Record<string, number> = {}
  let total = 0
  for (const f of files) {
    const rel = relative(SRC, f).replace(/\\/g, '/')
    const text = readFileSync(f, 'utf8')
    let n = 0
    for (const line of text.split('\n')) {
      const t = line.trim()
      if (t.startsWith('//') || t.startsWith('*') || t.startsWith('/*')) continue
      if (re.test(line)) n++
    }
    if (n) { byFile[rel] = n; total += n }
  }
  return { total, byFile }
}

export function countUppercaseTrackedEyebrows(): { total: number; byFile: Record<string, number> } {
  const files = walk(SRC)
  const upper = /\buppercase\b/
  const trackWide = /\btracking-(wide|wider|widest)\b|\btracking-\[/
  const byFile: Record<string, number> = {}
  let total = 0
  for (const f of files) {
    const rel = relative(SRC, f).replace(/\\/g, '/')
    const text = readFileSync(f, 'utf8')
    let n = 0
    for (const line of text.split('\n')) {
      const t = line.trim()
      if (t.startsWith('//') || t.startsWith('*') || t.startsWith('/*')) continue
      if (upper.test(line) && trackWide.test(line)) n++
    }
    if (n) { byFile[rel] = n; total += n }
  }
  return { total, byFile }
}


export interface InertUtilityHit {
  file: string
  line: number
  utility: string
  base: string
}

export type UtilityOracle = (candidate: string) => boolean

export async function loadUtilityOracle(): Promise<UtilityOracle> {
  const { __unstable__loadDesignSystem } = await import('tailwindcss')
  const require_ = createRequire(import.meta.url)
  const entry = join(SRC, 'shared/theme/tokens.css')
  const ds = await __unstable__loadDesignSystem(readFileSync(entry, 'utf8'), {
    base: dirname(entry),
    loadStylesheet: async (id: string, base: string) => {
      const path = id === 'tailwindcss' ? require_.resolve('tailwindcss/index.css') : resolve(base, id)
      return { path, base: dirname(path), content: readFileSync(path, 'utf8') }
    },
    loadModule: async (id: string) => {
      throw new Error(`inert-utility scan: unexpected JS import ${id} from tokens.css`)
    },
  })
  return (candidate: string) => ds.candidatesToCss([candidate])[0] !== null
}

function handAuthoredClasses(): Set<string> {
  const out = new Set<string>()
  const dir = join(SRC, "shared/theme")
  for (const entry of readdirSync(dir)) {
    if (!entry.endsWith('.css')) continue
    const text = readFileSync(join(dir, entry), 'utf8')
    for (const m of text.matchAll(/\.(-?[a-zA-Z_][\w-]*)/g)) out.add(m[1])
  }
  return out
}

function classAttributeRegions(text: string): { offset: number; body: string }[] {
  const out: { offset: number; body: string }[] = []
  const re = /\bclass(?:Name)?\s*=\s*/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    const i = m.index + m[0].length
    if (text[i] === '{') {
      let depth = 0
      let j = i
      for (; j < text.length; j++) {
        if (text[j] === '{') depth++
        else if (text[j] === '}') { depth--; if (!depth) break }
      }
      out.push({ offset: i + 1, body: text.slice(i + 1, j) })
    } else if (text[i] === '"' || text[i] === "'" || text[i] === '`') {
      const j = text.indexOf(text[i], i + 1)
      if (j > 0) out.push({ offset: i + 1, body: text.slice(i + 1, j) })
    }
  }
  return out
}

const CLASS_TOKEN_SPLIT = /[^A-Za-z0-9_@:./![\]%&>*+~(),#='"-]+|["'`]/
const SCANNED_PREFIX = /(?:^|:)!?-?(?:text|bg|border|rounded|gap)-/

export async function scanInertUtilities(): Promise<InertUtilityHit[]> {
  const isLive = await loadUtilityOracle()
  const handAuthored = handAuthoredClasses()
  const hits: InertUtilityHit[] = []
  for (const file of walk(SRC)) {
    const rel = relative(SRC, file).replace(/\\/g, '/')
    const text = readFileSync(file, 'utf8')
    const lineStarts = [0]
    for (let i = 0; i < text.length; i++) if (text[i] === '\n') lineStarts.push(i + 1)
    const lineAt = (idx: number) => {
      let lo = 0
      let hi = lineStarts.length - 1
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1
        if (lineStarts[mid] <= idx) lo = mid
        else hi = mid - 1
      }
      return lo + 1
    }
    for (const { offset, body } of classAttributeRegions(text)) {
      let cursor = 0
      for (const token of body.split(CLASS_TOKEN_SPLIT)) {
        const at = body.indexOf(token, cursor)
        cursor = at + token.length
        if (!token || !SCANNED_PREFIX.test(token)) continue
        const bare = token.replace(/^.*:/, '').replace(/^!/, '')
        const base = bare.replace(/\/(?:\d+|\[[^\]]*\])$/, '')
        if (handAuthored.has(base)) continue
        if (isLive(token)) continue
        hits.push({ file: rel, line: lineAt(offset + at), utility: token, base })
      }
    }
  }
  return hits
}


export function buildAuditPayload() {
  const res = scanDrift()
  const ranked = rankFiles(res)
  const a11y = scanA11y()
  return {
    totals: res.totals,
    byCategory: res.byCategory,
    ranked: ranked.slice(0, 40),
    a11y: {
      outlineNoneCount: a11y.outlineNoneCount,
      outlineNoneFiles: a11y.outlineNoneFiles.length,
      localFocusVisibleFiles: a11y.localFocusVisibleFiles.length,
      reducedMotionFiles: a11y.reducedMotionFiles.length,
      animatedFiles: a11y.animatedFiles,
      hasGlobalReducedMotion: a11y.hasGlobalReducedMotion,
      hasGlobalFocusRing: a11y.hasGlobalFocusRing,
    },
    byFile: res.byFile,
    primitivesByFile: res.primitivesByFile,
    drift: res.drift,
  }
}

export const AUDIT_JSON_PATH = ['..', 'docs', 'design', 'consistency-audit.json'] as const
