
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'


let REDUCED = false

Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) =>
    ({
      matches: REDUCED && query.includes('prefers-reduced-motion'),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
      onchange: null,
    }) as unknown as MediaQueryList,
})

const motionModule = (await import('./motion')) as unknown as Record<string, unknown>


type SpringKind = 'explicit' | 'inferred'

const SPRING_PARAM_KEYS = ['stiffness', 'damping', 'mass', 'bounce', 'restSpeed', 'restDelta'] as const

function springKind(value: unknown): SpringKind | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null
  const o = value as Record<string, unknown>
  if (o['type'] === 'spring') return 'explicit'
  if (o['type'] === undefined && SPRING_PARAM_KEYS.some((k) => typeof o[k] === 'number')) return 'inferred'
  return null
}

const NOOP = (): void => {}

const ARG_TUPLES: readonly unknown[][] = [[], [NOOP], [0], [0, 0], [1, 1], [1e6, 1e6], [0.5, 0.5]]

const MAX_DEPTH = 6

interface Probe {
  values: Map<string, unknown>
  uninvokable: string[]
  nestedFailures: string[]
  exportNames: string[]
}

function visit(value: unknown, path: string, out: Probe, ancestors: readonly object[], depth: number): void {
  if (depth > MAX_DEPTH) return
  if (typeof value === 'function') {
    if (value === NOOP) return
    let resolved: unknown
    try {
      resolved = (value as () => unknown)()
    } catch {
      out.nestedFailures.push(path)
      return
    }
    visit(resolved, `${path}()`, out, ancestors, depth + 1)
    return
  }
  if (value === null || typeof value !== 'object') return
  if (ancestors.includes(value as object)) return
  out.values.set(path, value)
  const next = [...ancestors, value as object]
  if (Array.isArray(value)) {
    value.forEach((v, i) => visit(v, `${path}[${i}]`, out, next, depth + 1))
    return
  }
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    visit(v, `${path}.${k}`, out, next, depth + 1)
  }
}

function probe(reduced: boolean): Probe {
  REDUCED = reduced
  const out: Probe = { values: new Map(), uninvokable: [], nestedFailures: [], exportNames: [] }
  for (const [name, value] of Object.entries(motionModule)) {
    out.exportNames.push(name)
    if (typeof value === 'function') {
      let invoked = 0
      ARG_TUPLES.forEach((args, i) => {
        let result: unknown
        try {
          result = (value as (...a: unknown[]) => unknown)(...args)
        } catch {
          return
        }
        invoked += 1
        visit(result, `${name}(#${i})`, out, [], 0)
      })
      if (invoked === 0) out.uninvokable.push(name)
    } else {
      visit(value, name, out, [], 0)
    }
  }
  return out
}

function springPaths(p: Probe): string[] {
  return [...p.values.entries()]
    .filter(([, v]) => springKind(v) !== null)
    .map(([path]) => path)
    .sort()
}

const ALLOWED = probe(false)
const REDUCED_PROBE = probe(true)

describe('reduced motion is an app-wide property of design/motion.ts, not a per-preset one', () => {
  it('reaches every export — nothing is skipped, and a new export cannot hide', () => {
    expect(
      ALLOWED.exportNames.length,
      `Only ${ALLOWED.exportNames.length} exports were enumerated from design/motion.ts. ` +
        `That is too few to be the real module — the import or the reflection is broken.`,
    ).toBeGreaterThanOrEqual(15)

    expect(
      ALLOWED.uninvokable,
      `These exports threw for EVERY argument tuple, so nothing about them was measured. ` +
        `Add a tuple to ARG_TUPLES that satisfies them — do not leave them unprobed.`,
    ).toEqual([])

    expect(
      ALLOWED.nestedFailures.concat(REDUCED_PROBE.nestedFailures),
      'A nested function (variant resolver) threw while being resolved, so its transition was never inspected.',
    ).toEqual([])

    expect(REDUCED_PROBE.exportNames).toEqual(ALLOWED.exportNames)
  })

  it('the media-query stub actually takes — both passes measure what they claim', () => {
    const prefersReducedMotion = motionModule['prefersReducedMotion'] as () => boolean
    REDUCED = true
    expect(prefersReducedMotion()).toBe(true)
    REDUCED = false
    expect(prefersReducedMotion()).toBe(false)
  })

  it('finds real springs when motion is ALLOWED — the vacuity floor for the assertion below', () => {
    const found = springPaths(ALLOWED)
    expect(
      found.length,
      `The walker found ${found.length} spring-shaped transitions with motion allowed. ` +
        `It must find several, or it is not actually reading the module's transitions.`,
    ).toBeGreaterThanOrEqual(6)
    expect(found, 'Known-positive sample: the raw spatial tier must be visible to the walker.').toContain(
      'spring.spatialFast',
    )
    expect(
      found.some((p) => p.startsWith('physics.')),
      'Known-positive sample: the named physics presets must be visible to the walker.',
    ).toBe(true)
  })

  it('NO export yields a spring under prefers-reduced-motion — every family, not just the gated one', () => {
    const offenders = springPaths(REDUCED_PROBE).map((path) => `${path} (${springKind(REDUCED_PROBE.values.get(path))})`)
    expect(
      offenders,
      `These transitions exported by design/motion.ts still animate as springs when the user ` +
        `asked for reduced motion:\n  ${offenders.join('\n  ')}\n` +
        `A transition family must route through the same gate as physics.* — being a static ` +
        `object literal instead of a getter is exactly how a family escapes the off-switch.`,
    ).toEqual([])
  })

  it('every collapsed spring states a non-spring TYPE, so a leftover stiffness cannot re-infer one', () => {
    const leaky: string[] = []
    for (const path of springPaths(ALLOWED)) {
      if (!REDUCED_PROBE.values.has(path)) continue
      const collapsed = REDUCED_PROBE.values.get(path) as Record<string, unknown>
      const type = collapsed['type']
      if (typeof type !== 'string' || type === 'spring') {
        leaky.push(`${path} -> type=${JSON.stringify(type)}`)
      }
    }
    expect(
      leaky,
      `These reduced-motion transitions carry no explicit non-spring \`type\`, so a call site ` +
        `spreading a leftover spring parameter onto them re-infers a spring:\n  ${leaky.join('\n  ')}`,
    ).toEqual([])
  })
})


const CWD = process.cwd()
const SRC_ROOT = join(CWD, 'src')
const MOTION_MODULE = 'src/shared/theme/motion.ts'

function collectSources(root: string): string[] {
  const out: string[] = []
  const readSafe = (dir: string) => {
    try {
      return readdirSync(dir, { withFileTypes: true })
    } catch {
      return []
    }
  }
  const walkDir = (dir: string): void => {
    for (const e of readSafe(dir)) {
      const full = join(dir, e.name)
      if (e.isDirectory()) {
        if (e.name === 'node_modules' || e.name === 'dist') continue
        walkDir(full)
        continue
      }
      if (!/\.tsx?$/.test(e.name)) continue
      if (/\.(test|spec)\.tsx?$/.test(e.name)) continue
      out.push(full.slice(CWD.length + 1).split('\\').join('/'))
    }
  }
  walkDir(root)
  return out.sort()
}

function blankComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:])\/\/[^\n]*/g, (m, lead: string) => lead + ' '.repeat(m.length - lead.length))
}

const LITERAL_SPRING = /\btype\s*:\s*['"]spring['"]/g
const SPRING_PARAM = /\b(?:stiffness|damping|bounce)\s*:/g

interface Hit {
  file: string
  line: number
  text: string
}

function scan(files: string[], pattern: RegExp): Hit[] {
  const hits: Hit[] = []
  for (const file of files) {
    let raw: string
    try {
      raw = readFileSync(join(CWD, file), 'utf8')
    } catch {
      continue
    }
    blankComments(raw)
      .split('\n')
      .forEach((line, i) => {
        if (new RegExp(pattern.source).test(line)) {
          hits.push({ file, line: i + 1, text: raw.split('\n')[i].trim() })
        }
      })
  }
  return hits
}

function tally(hits: Hit[]): string[] {
  const counts = new Map<string, number>()
  for (const h of hits) counts.set(h.file, (counts.get(h.file) ?? 0) + 1)
  return [...counts.entries()].map(([f, n]) => `${f}:${n}`).sort()
}

const SOURCES = collectSources(SRC_ROOT)
const OUTSIDE_MODULE = SOURCES.filter((f) => f !== MOTION_MODULE)

const SPRING_PARAM_BASELINE: readonly string[] = [
  'src/shared/ui/ComposerStage.tsx:1',
  'src/shared/ui/SidePanel.tsx:1',
  'src/shared/ui/motion/vocabulary.ts:1',
]

describe('no module outside design/motion.ts mints spring physics', () => {
  it('writes no literal spring transition', () => {
    const offenders = scan(OUTSIDE_MODULE, LITERAL_SPRING).map((h) => `${h.file}:${h.line}  ${h.text}`)
    expect(
      offenders,
      `A literal spring transition was written outside design/motion.ts:\n  ${offenders.join('\n  ')}\n` +
        `Springs are minted in ONE place so ONE gate can zero them. Use a physics.* preset.`,
    ).toEqual([])
  })

  it('mints bare spring physics only at the reviewed sites (shrink-only baseline)', () => {
    expect(
      tally(scan(OUTSIDE_MODULE, SPRING_PARAM)),
      `The set of modules writing bare spring physics changed. A NEW entry must be reviewed: it ` +
        `is only safe if it spreads a gated preset rather than building a spring from scratch. ` +
        `A REMOVED entry must be deleted from SPRING_PARAM_BASELINE in the same commit.`,
    ).toEqual([...SPRING_PARAM_BASELINE])
  })

  it('every baselined site re-tunes a gated preset rather than building a spring', () => {
    const fromScratch = scan(OUTSIDE_MODULE, SPRING_PARAM)
      .filter((h) => !h.text.includes('...'))
      .map((h) => `${h.file}:${h.line}  ${h.text}`)
    expect(
      fromScratch,
      `These sites write spring physics WITHOUT spreading a gated preset, so nothing collapses ` +
        `them under reduced motion:\n  ${fromScratch.join('\n  ')}`,
    ).toEqual([])
  })
})

describe('census self-checks — the floors that stop a silent pass', () => {
  it('scanned a non-trivial corpus, including known files', () => {
    expect(
      SOURCES.length,
      `The census corpus is ${SOURCES.length} files. A mistyped root yields an empty corpus, ` +
        `and an empty corpus passes every offender assertion in this file.`,
    ).toBeGreaterThanOrEqual(300)
    expect(SOURCES).toContain(MOTION_MODULE)
    expect(SOURCES).toContain('src/shared/ui/SidePanel.tsx')
    expect(SOURCES.some((f) => /\.test\.tsx?$/.test(f)), 'test files must be excluded').toBe(false)
  })

  it('an absent root yields an empty corpus, which the floor above rejects', () => {
    expect(collectSources(join(CWD, 'src-does-not-exist'))).toEqual([])
    expect(collectSources(join(CWD, 'src-does-not-exist')).length).toBeLessThan(300)
  })

  it('both patterns match known-positive code in the sanctioned module', () => {
    const literal = scan([MOTION_MODULE], LITERAL_SPRING)
    const params = scan([MOTION_MODULE], SPRING_PARAM)
    expect(literal.length, 'LITERAL_SPRING must match the module that mints springs').toBeGreaterThanOrEqual(3)
    expect(params.length, 'SPRING_PARAM must match the module that mints springs').toBeGreaterThanOrEqual(3)
  })

  it('comment blanking removes prose that would otherwise be counted as code', () => {
    const raw = readFileSync(join(CWD, 'src/shared/ui/motion/vocabulary.ts'), 'utf8')
    const rawHits = raw.split('\n').filter((l) => new RegExp(SPRING_PARAM.source).test(l)).length
    const blankedHits = blankComments(raw).split('\n').filter((l) => new RegExp(SPRING_PARAM.source).test(l)).length
    expect(rawHits, 'the control file must carry commented mentions, or it proves nothing').toBeGreaterThan(1)
    expect(blankedHits, 'blanking must remove the commented mentions').toBeLessThan(rawHits)
    expect(blankedHits).toBe(1)
  })

  it('blanking preserves line numbering and does not swallow code after a URL', () => {
    const src = ['const a = 1 // stiffness: 9', 'const u = "https://x" // c', '/* stiffness: 9', ' */', 'const b = 2']
      .join('\n')
    const blanked = blankComments(src)
    expect(blanked.split('\n')).toHaveLength(5)
    expect(new RegExp(SPRING_PARAM.source).test(blanked)).toBe(false)
    expect(blanked.split('\n')[1]).toContain('https://x')
    expect(blanked.split('\n')[4]).toBe('const b = 2')
  })
})

const GATED_READ = /\b(?:spring|physics)\.[A-Za-z_]\w*/

function moduleScopeInitializers(text: string): { name: string; body: string; line: number }[] {
    const lines = text.split('\n')
    const out: { name: string; body: string; line: number }[] = []
    for (let i = 0; i < lines.length; i++) {
        const m = /^const\s+([A-Za-z_]\w*)\s*(?::[^=]+)?=\s*(.*)$/.exec(lines[i])
        if (!m) continue
        const head = m[2]
        if (head.includes('=>') || head.startsWith('function')) continue
        let depth = 0
        let started = false
        const body: string[] = []
        for (let j = i; j < lines.length; j++) {
            body.push(lines[j])
            for (const ch of lines[j]) {
                if (ch === '{' || ch === '[' || ch === '(') { depth++; started = true }
                else if (ch === '}' || ch === ']' || ch === ')') depth--
            }
            if (started && depth <= 0) break
            if (!started && j > i) break
        }
        out.push({ name: m[1], body: body.join('\n'), line: i + 1 })
    }
    return out
}

describe('a gated preset is never read at module scope', () => {
    it('sees the shape it exists to catch, and NOT the one that fooled a line window', () => {
        const offender = moduleScopeInitializers(
            "const travelEnter = {\n  animate: { transition: spring.spatialSlow },\n}\n",
        ).filter((d) => GATED_READ.test(d.body))
        expect(offender.map((d) => d.name)).toEqual(['travelEnter'])

        const innocent = moduleScopeInitializers(
            "const OVERLAY_FOCUS = 'focus:ring-primary'\n\nfunction Clear() {\n" +
                "  return <m.div transition={physics.snappy} />\n}\n",
        ).filter((d) => GATED_READ.test(d.body))
        expect(innocent, 'a line window walked into the next declaration and called it an offender')
            .toEqual([])

        expect(
            moduleScopeInitializers(
                "const travelEnter = () => ({\n  animate: { transition: spring.spatialSlow },\n})\n",
            ).filter((d) => GATED_READ.test(d.body)),
        ).toEqual([])
    })

    it('no source file reads a gated preset at module scope', () => {
        const files = collectSources(SRC_ROOT)
        expect(files.length, 'the corpus is empty — this census proves nothing').toBeGreaterThan(300)
        const offenders: string[] = []
        for (const file of files) {
            const text = blankComments(readFileSync(join(CWD, file), 'utf8'))
            for (const decl of moduleScopeInitializers(text)) {
                if (GATED_READ.test(decl.body)) offenders.push(`${file}:${decl.line} ${decl.name}`)
            }
        }
        expect(offenders, [
            'a gated transition preset is read at module scope, which resolves the',
            'reduced-motion gate once at import and freezes it for the session.',
            'Wrap the initializer in a function so the getter is read per render.',
        ].join(' ')).toEqual([])
    })
})
