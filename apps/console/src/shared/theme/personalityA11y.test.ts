
import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { SCHEMES } from './schemes'
import {
  DEFAULT_PERSONALITY,
  PERSONALITIES,
  PERSONALITY_DIAL_TOKENS,
  getPersonality,
  getShellElement,
  resolvePersonality,
  type Personality,
  type PersonalityDials,
} from './personalities'
import { getErrorTreatment } from './errorTreatments'
import { CUES } from './soundCues'
import { TOKENS } from './tokenRegistry'

const SCHEME_IDS = new Set(SCHEMES.map((s) => s.id))

const ALLOWED_BEHAVIOR_KEYS = new Set([
  'displayName',
  'wordmarkLabel',
  'faviconHref',
  'personaSnippet',
  'uiDensity',
  'documentTitle',
  'shellElement',
  'errorTreatment',
  'soundCues',
  'dials',
])

const ALLOWED_DENSITY = new Set(['comfortable', 'dense', 'cli'])
const CUE_POINTS = new Set(['turn_complete', 'approval_needed', 'error'])

interface Rail {
  id: string
  title: string
  check: (p: Personality) => string | null
}

const RAILS: Rail[] = [
  {
    id: 'baseScheme',
    title: 'names a baseScheme that exists in SCHEMES',
    check: (p) =>
      SCHEME_IDS.has(p.baseScheme) ? null : `${p.id} → unknown baseScheme '${p.baseScheme}'`,
  },
  {
    id: 'behaviorKeys',
    title: 'keeps every behavior key inside the closed set',
    check: (p) => {
      const bad = Object.keys(p.behavior).filter((k) => !ALLOWED_BEHAVIOR_KEYS.has(k))
      return bad.length ? `${p.id}.behavior has un-allowlisted key(s) ${bad.join(', ')}` : null
    },
  },
  {
    id: 'uiDensity',
    title: 'maps uiDensity onto the existing density axis',
    check: (p) =>
      !p.behavior.uiDensity || ALLOWED_DENSITY.has(p.behavior.uiDensity)
        ? null
        : `${p.id} → unknown uiDensity '${p.behavior.uiDensity}'`,
  },
  {
    id: 'favicon',
    title: 'uses a bundled local favicon, never a remote URL',
    check: (p) => {
      const href = p.behavior.faviconHref
      if (!href) return null
      return href.startsWith('/') && !/^https?:/i.test(href)
        ? null
        : `${p.id} → non-local faviconHref '${href}'`
    },
  },
  {
    id: 'personaSnippet',
    title: 'follows the bundled persona-<id> naming the backend validates',
    check: (p) => {
      const s = p.behavior.personaSnippet
      if (!s) return null
      return s === `persona-${p.id}` ? null : `${p.id} → personaSnippet '${s}' breaks the naming`
    },
  },
  {
    id: 'shellElement',
    title: 'names a shellElement that resolves in its closed map',
    check: (p) => {
      const id = p.behavior.shellElement
      if (!id) return null
      return getShellElement(id) ? null : `${p.id} → dangling shellElement '${id}'`
    },
  },
  {
    id: 'errorTreatment',
    title: 'names an errorTreatment that resolves in its closed map',
    check: (p) => {
      const id = p.behavior.errorTreatment
      if (!id) return null
      return getErrorTreatment(id) ? null : `${p.id} → dangling errorTreatment '${id}'`
    },
  },
  {
    id: 'cueVoices',
    title: 're-voices only real cue POINTS, and only with registered voices',
    check: (p) => {
      const map = p.behavior.soundCues
      if (!map) return null
      for (const [point, voice] of Object.entries(map)) {
        if (!CUE_POINTS.has(point)) return `${p.id} → '${point}' is not a cue point`
        if (!Object.hasOwn(CUES, voice)) return `${p.id} → unregistered cue voice '${voice}'`
      }
      return null
    },
  },
  {
    id: 'dialTokens',
    title: 'presets only dials that name a real token, within that token’s range',
    check: (p) => {
      const dials = p.behavior.dials
      if (!dials) return null
      for (const [dial, value] of Object.entries(dials)) {
        const varName = PERSONALITY_DIAL_TOKENS[dial as keyof PersonalityDials]
        if (!varName) return `${p.id} → '${dial}' is not a declared dial`
        const token = TOKENS.find((t) => t.varName === varName)
        if (!token) return `${p.id} → dial '${dial}' names missing token '${varName}'`
        if (token.kind === 'color' || !token.runtimeKey) {
          return `${p.id} → dial '${dial}' names '${varName}', which feeds no runtime dial`
        }
        if (typeof value === 'number') {
          if (token.kind !== 'scalar') return `${p.id} → dial '${dial}' is not a scalar token`
          if (value < token.min || value > token.max) {
            return `${p.id} → dial '${dial}'=${value} outside ${token.min}..${token.max}`
          }
        } else {
          if (token.kind !== 'select') return `${p.id} → dial '${dial}' is not a select token`
          if (!token.options.includes(value)) {
            return `${p.id} → dial '${dial}'='${value}' is not an offered value`
          }
        }
      }
      return null
    },
  },
  {
    id: 'labelAndHint',
    title: 'carries a human label and hint',
    check: (p) =>
      p.label.trim() && p.hint.trim() ? null : `${p.id} → empty label or hint`,
  },
]

const VALID: Personality = {
  id: 'fixture',
  label: 'Fixture',
  hint: 'A valid entry, used as the base for every broken one below.',
  baseScheme: SCHEMES[0].id,
  behavior: {},
}

const BROKEN: Record<string, Personality> = {
  baseScheme: { ...VALID, baseScheme: 'no-such-scheme' },
  behaviorKeys: { ...VALID, behavior: { injectedCss: 'body{display:none}' } as never },
  uiDensity: { ...VALID, behavior: { uiDensity: 'roomy' as never } },
  favicon: { ...VALID, behavior: { faviconHref: 'https://cdn.example.com/f.svg' } },
  personaSnippet: { ...VALID, behavior: { personaSnippet: 'persona-lumon' } },
  shellElement: { ...VALID, behavior: { shellElement: 'no-such-element' as never } },
  errorTreatment: { ...VALID, behavior: { errorTreatment: 'no-such-treatment' as never } },
  cueVoices: { ...VALID, behavior: { soundCues: { turn_complete: 'ka-ching' as never } } },
  dialTokens: { ...VALID, behavior: { dials: { dotShape: 'hexagram' as never } } },
  labelAndHint: { ...VALID, hint: '   ' },
}

const violations = (list: Personality[], rail: Rail) =>
  list.map(rail.check).filter((v): v is string => v !== null)

const CASES = RAILS.map((r) => [`${r.id} — ${r.title}`, r] as const)

describe('the real registry satisfies every structural invariant', () => {
  it.each(CASES)('%s', (_title, rail) => {
    expect(violations(PERSONALITIES, rail)).toEqual([])
  })

  it('has at least the default plus two alternatives to prove switching', () => {
    expect(PERSONALITIES.length).toBeGreaterThanOrEqual(3)
  })

  it('declares unique ids', () => {
    const ids = PERSONALITIES.map((p) => p.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})

describe('every invariant goes red on a broken entry', () => {
  it('every rail has a falsifying fixture, and every fixture a rail', () => {
    expect(Object.keys(BROKEN).sort()).toEqual(RAILS.map((r) => r.id).sort())
  })

  it('the base fixture is itself CLEAN — or every mutation below proves nothing', () => {
    for (const rail of RAILS) expect(rail.check(VALID), rail.id).toBeNull()
  })

  it.each(CASES)('%s — flags its own broken fixture', (_title, rail) => {
    expect(rail.check(BROKEN[rail.id]), `${rail.id} did not flag its own fixture`).not.toBeNull()
  })

  it.each(CASES)('%s — its fixture trips no OTHER rail', (_title, rail) => {
    const tripped = RAILS.filter((r) => r.check(BROKEN[rail.id]) !== null).map((r) => r.id)
    expect(tripped).toEqual([rail.id])
  })
})

describe('the conditional rails scan a non-empty population', () => {
  const declares = (pick: (p: Personality) => unknown) => PERSONALITIES.filter((p) => pick(p)).map((p) => p.id)

  it.each([
    ['faviconHref', (p: Personality) => p.behavior.faviconHref],
    ['personaSnippet', (p: Personality) => p.behavior.personaSnippet],
    ['uiDensity', (p: Personality) => p.behavior.uiDensity],
    ['shellElement', (p: Personality) => p.behavior.shellElement],
    ['errorTreatment', (p: Personality) => p.behavior.errorTreatment],
    ['soundCues', (p: Personality) => p.behavior.soundCues],
    ['dials', (p: Personality) => p.behavior.dials],
  ])('at least one personality declares %s', (_field, pick) => {
    expect(declares(pick)).not.toEqual([])
  })

  it('both proof identities are fully specified — every behavior except the default’s', () => {
    const proofs = PERSONALITIES.filter((p) => p.id !== DEFAULT_PERSONALITY)
    const covered = new Set(proofs.flatMap((p) => Object.keys(p.behavior)))
    expect([...ALLOWED_BEHAVIOR_KEYS].filter((k) => !covered.has(k))).toEqual([])
  })

  it('every registered cue voice is reachable from some personality or is a cue point', () => {
    const declared = new Set<string>(
      PERSONALITIES.flatMap((p) => Object.values(p.behavior.soundCues ?? {})),
    )
    const unreachable = Object.keys(CUES).filter(
      (voice) => !CUE_POINTS.has(voice) && !declared.has(voice),
    )
    expect(unreachable, 'these cue recipes can never play').toEqual([])
  })
})


const WEB = process.cwd() // vitest runs from web/
const CUE_MODULE = 'src/shared/theme/soundCues.ts'

function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:])\/\/[^\n]*/g, '$1')
}

function sourceFiles(dir = join(WEB, 'src')): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...sourceFiles(p))
    else if (/\.tsx?$/.test(entry) && !/\.(test|spec)\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

function functionBody(code: string, name: string): string {
  const start = code.indexOf(`function ${name}(`)
  if (start < 0) return ''
  const open = code.indexOf('{', start)
  if (open < 0) return ''
  let depth = 0
  for (let i = open; i < code.length; i++) {
    if (code[i] === '{') depth++
    else if (code[i] === '}' && --depth === 0) return code.slice(open + 1, i)
  }
  return ''
}

describe('a cue can only sound through the gate', () => {
  const raw = readFileSync(join(WEB, CUE_MODULE), 'utf8')
  const code = stripComments(raw)
  const body = functionBody(code, 'playCue')

  it('the extractor found real code, and only playCue’s', () => {
    expect(body.length, 'playCue’s body was not found').toBeGreaterThan(80)
    expect(body).toContain('synth(')
    expect(functionBody(code, 'thisFunctionDoesNotExist')).toBe('')
    expect(stripComments('x /* soundCuesEnabled() */ y')).not.toMatch(/soundCuesEnabled\(\)/)
  })

  it('playCue checks all three suppressors BEFORE it reaches the synth', () => {
    const upToSynth = body.slice(0, body.indexOf('synth('))
    expect(upToSynth, 'the master toggle').toMatch(/soundCuesEnabled\(\)/)
    expect(upToSynth, 'reduced motion').toMatch(/prefersReducedMotion\(\)/)
    expect(upToSynth, 'a hidden tab').toMatch(/document\.hidden/)
  })

  it('playCue is the ONLY caller of synth — no second, ungated path', () => {
    const total = [...code.matchAll(/(?<!function )\bsynth\(/g)].length
    const inside = [...body.matchAll(/\bsynth\(/g)].length
    expect(total, 'the counter found no call at all').toBeGreaterThan(0)
    expect(total - inside, 'something outside playCue calls synth').toBe(0)
  })

  it('the synthesiser and the recipes-to-sound path are module-private', () => {
    expect(code, 'synth must not be exported').not.toMatch(/export\s+(function\s+)?synth\b/)
    expect(code).toMatch(/^function synth\(/m)
  })

  it('no module outside the cue module can synthesise a tone', () => {
    const offenders = sourceFiles()
      .map((f) => f.slice(WEB.length + 1))
      .filter((rel) => rel !== CUE_MODULE)
      .filter((rel) => /createOscillator\s*\(/.test(stripComments(readFileSync(join(WEB, rel), 'utf8'))))
    expect(offenders, 'a tone built here would answer to no toggle — call playCue instead')
      .toEqual([])
  })

  it('the source sweep is real: exactly one module synthesises, and it is the cue module', () => {
    const all = sourceFiles().map((f) => f.slice(WEB.length + 1))
    expect(all.length, 'the walker must find web/src').toBeGreaterThan(200)
    expect(all, 'the sweep must include the cue module itself').toContain(CUE_MODULE)
    const withOsc = all.filter((rel) =>
      /createOscillator\s*\(/.test(readFileSync(join(WEB, rel), 'utf8')),
    )
    expect(withOsc).toEqual([CUE_MODULE])
  })
})

describe('the default identity is restorable', () => {
  it('the default id resolves to a real entry', () => {
    expect(getPersonality(DEFAULT_PERSONALITY)).toBeDefined()
  })

  it('the default carries no assistant rename', () => {
    expect(getPersonality(DEFAULT_PERSONALITY)?.behavior.displayName).toBeUndefined()
  })

  it('the default declares no cue voice, no dial, no shell element, no treatment', () => {
    const b = getPersonality(DEFAULT_PERSONALITY)!.behavior
    expect(b.soundCues).toBeUndefined()
    expect(b.dials).toBeUndefined()
    expect(b.shellElement).toBeUndefined()
    expect(b.errorTreatment).toBeUndefined()
  })

  it('an unknown or removed id falls back to the default, never a broken state', () => {
    expect(resolvePersonality('was-removed-in-a-later-release').id).toBe(DEFAULT_PERSONALITY)
    expect(resolvePersonality(undefined).id).toBe(DEFAULT_PERSONALITY)
  })
})
