import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

const TILES = 'features/dashboard/PinnedTiles.tsx'

const BODY_GATE = /\{\s*body\s*\?/

const BODY_RENDERERS = ['<WidgetFrame', '<GenUiWidget'] as const

const sourceOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

function isPlainNumber(expression: string, src: string): boolean {
  if (/^-?\d*\.?\d+$/.test(expression)) return true
  if (!/^[A-Za-z_$][\w$]*$/.test(expression)) return false
  return new RegExp(
    `\\b(?:const|let|var)\\s+${expression}(?:\\s*:\\s*number)?\\s*=\\s*-?\\d*\\.?\\d+\\s*(?:;|$)`,
    'm',
  ).test(src)
}

function liquidTag(src: string): string | null {
  return src.match(/<LiquidShape\b[\s\S]*?\/>/)?.[0] ?? null
}

describe('the liquid state transition is adopted in the product', () => {
  it('finds the surface it scans, with the tile it scans for', () => {
    const src = sourceOf(TILES)
    expect(src.split('\n').length, `${TILES} is too short to be the pinned-tiles band`)
      .toBeGreaterThan(100)
    expect(src).toContain('data-testid="pinned-tiles"')
    expect(src).toMatch(/function PinnedTile\(/)
    expect(src, `${TILES} must still switch its body on one conditional`)
      .toMatch(BODY_GATE)
    for (const renderer of BODY_RENDERERS) {
      expect(src.slice(src.search(BODY_GATE)), `${renderer} must render inside the body conditional`)
        .toContain(renderer)
    }
  })

  it('reaches the primitive through the shared motion barrel', () => {
    const src = sourceOf(TILES)
    expect(src, "import LiquidShape from '../../shared/ui/motion', not from ui/motion/LiquidShape")
      .toMatch(/import \{[^}]*\bLiquidShape\b[^}]*\} from '\.\.\/\.\.\/shared\/ui\/motion'/)
    expect(src, `${TILES} must render <LiquidShape>, not merely import it`)
      .toMatch(/<LiquidShape\b/)
    expect(src, `${TILES} must not compose its own family timing`)
      .not.toMatch(/\b(familySpring|familyTween|MORPH_FAMILY)\b/)
  })

  it('hosts the morph OUTSIDE the branch that unmounts on the state it depicts', () => {
    const src = sourceOf(TILES)
    const component = src.indexOf('function PinnedTile(')
    const liquid = src.indexOf('<LiquidShape')
    const bodyGate = src.search(BODY_GATE)
    expect(liquid, 'the morph must be inside PinnedTile, not the band above it')
      .toBeGreaterThan(component)
    expect(liquid, 'the morph must precede the body-switching conditional')
      .toBeLessThan(bodyGate)
    expect(src.slice(bodyGate), 'no LiquidShape may sit inside the conditional body')
      .not.toMatch(/\bLiquidShape\b/)
  })

  it('depicts unsettled→settled as blob→squircle, off real state', () => {
    const tag = liquidTag(sourceOf(TILES))
    expect(tag, 'the <LiquidShape> tag must be self-closing so this rail can scope to it')
      .not.toBeNull()
    expect(tag!.length, 'the matched tag ran past the call site').toBeLessThan(400)
    expect(tag).toMatch(/\bfrom="blob"/)
    expect(tag).toMatch(/\bto="squircle"/)
    expect(tag).toMatch(/\bactive=\{/)
    expect(tag, 'active must come from state, not a literal')
      .not.toMatch(/\bactive=\{\s*(?:true|false)\s*\}/)
  })

  it('passes a plain-number intensity and no hex tint', () => {
    const src = sourceOf(TILES)
    const tag = liquidTag(src)
    expect(tag, 'the <LiquidShape> tag must be self-closing so this rail can scope to it')
      .not.toBeNull()
    const intensity = tag!.match(/\bintensity=\{\s*([^}]+?)\s*\}/)?.[1]
    expect(intensity, 'intensity must be passed explicitly at this call site').toBeTruthy()
    expect(intensity, 'intensity must not be pre-scaled — the primitive applies expr() itself')
      .not.toMatch(/\bexpr/)
    expect(
      isPlainNumber(intensity!, src),
      `intensity={${intensity}} must be a number, or a name this module declares as one`,
    ).toBe(true)
    expect(tag!, 'no hex colour at a motion call site').not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })

  it('never leaves the depicted state to the decoration alone', () => {
    const src = sourceOf(TILES)
    expect(src, 'the loading state must still be carried by text, not only by the blob')
      .toContain('Loading tile')
  })

  it('LiquidShape has at least one call site that is not a test fixture', () => {
    const hits: string[] = []
    const walk = (dir: string) => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const p = join(dir, e.name)
        if (e.isDirectory()) { walk(p); continue }
        if (!/\.tsx?$/.test(e.name) || /\.(test|spec)\.tsx?$/.test(e.name)) continue
        if (p.startsWith(join(SRC, 'shared/ui/motion'))) continue
        if (/<LiquidShape\b/.test(readFileSync(p, 'utf8'))) hits.push(p.slice(SRC.length + 1))
      }
    }
    walk(SRC)
    expect(hits, 'LiquidShape is inert again — no product surface renders it').not.toHaveLength(0)
    expect(hits).toContain(TILES)
  })
})
