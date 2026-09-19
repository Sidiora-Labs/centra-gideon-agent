
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ERROR_TREATMENTS, getErrorTreatment, treatmentPaint, type ErrorTreatmentId } from './errorTreatments'
import { PERSONALITIES } from './personalities'

function luminance(hex: string): number {
  const h = hex.replace('#', '')
  const chan = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * chan(0) + 0.7152 * chan(2) + 0.0722 * chan(4)
}
function contrast(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

const AA = 4.5

function blocks(): { dark: string; light: string } {
  const src = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
  const at = src.search(/\.light\s*\{/)
  if (at < 0) throw new Error('could not find the .light rule block in tokens.css')
  return { dark: src.slice(0, at), light: src.slice(at) }
}

function tokenValue(block: string, name: string): string {
  const m = block.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`))
  if (!m) throw new Error(`${name} has no direct hex value in this tokens.css block`)
  return m[1]
}

const BLOCKS = blocks()
const MODES = ['dark', 'light'] as const

describe('the treatment map is closed, complete, and non-vacuous', () => {
  it('every id in the map keys its own entry (no copy-paste id drift)', () => {
    for (const [id, t] of Object.entries(ERROR_TREATMENTS)) expect(t.id).toBe(id)
  })

  it('at least one personality declares a treatment, so the skin tests are not vacuous', () => {
    const declared = PERSONALITIES.filter((p) => p.behavior.errorTreatment)
    expect(declared.length).toBeGreaterThan(0)
  })

  it('every declared treatment id resolves in the closed map', () => {
    for (const p of PERSONALITIES) {
      const id = p.behavior.errorTreatment
      if (id) expect(getErrorTreatment(id), `${p.id} → ${id}`).not.toBeNull()
    }
  })

  it('an unknown or absent id resolves to no treatment, never a throw', () => {
    expect(getErrorTreatment(undefined)).toBeNull()
    expect(getErrorTreatment('removed-in-a-later-release')).toBeNull()
    expect(getErrorTreatment('')).toBeNull()
    expect(treatmentPaint(null)).toBeNull()
  })

  it('an INHERITED key is not a treatment either, and does not throw downstream', () => {
    for (const inherited of ['constructor', 'toString', 'hasOwnProperty', '__proto__', 'valueOf']) {
      expect(getErrorTreatment(inherited), inherited).toBeNull()
      expect(() => treatmentPaint(getErrorTreatment(inherited)), inherited).not.toThrow()
    }
  })

  it('a treatment carries presentation ONLY — no copy, action or role slot', () => {
    const ALLOWED = new Set(['id', 'label', 'surfaceClass', 'iconClass', 'paint'])
    for (const t of Object.values(ERROR_TREATMENTS)) {
      for (const key of Object.keys(t)) expect(ALLOWED.has(key), `${t.id}.${key}`).toBe(true)
      expect(Object.keys(t.paint).sort()).toEqual(['bg', 'icon', 'ink'])
    }
  })

  it('treatment-owned classes never borrow the ignored deprecated Lucide alias', () => {
    for (const t of Object.values(ERROR_TREATMENTS)) {
      expect(t.surfaceClass.split(/\s+/), `${t.id}.surfaceClass`).not.toContain('lucide-alert-triangle')
      expect(t.iconClass.split(/\s+/), `${t.id}.iconClass`).not.toContain('lucide-alert-triangle')
    }
  })

  it('paints with design tokens, never colour literals', () => {
    for (const t of Object.values(ERROR_TREATMENTS)) {
      for (const [slot, token] of Object.entries(t.paint)) {
        expect(token, `${t.id}.paint.${slot}`).toMatch(/^--color-[a-z-]+$/)
      }
    }
  })

  it('renders paint as var() references so the token indirection survives', () => {
    const t = ERROR_TREATMENTS['terminal-frame']
    expect(treatmentPaint(t)).toEqual({
      background: `var(${t.paint.bg})`,
      color: `var(${t.paint.ink})`,
    })
  })
})

describe('every treatment meets WCAG AA in BOTH modes', () => {
  it('the token file yielded real, per-mode values (not vacuously green)', () => {
    expect(tokenValue(BLOCKS.dark, '--color-surface-container')).not.toBe(
      tokenValue(BLOCKS.light, '--color-surface-container'),
    )
    expect(tokenValue(BLOCKS.dark, '--color-danger')).not.toBe(tokenValue(BLOCKS.light, '--color-danger'))
  })

  for (const id of Object.keys(ERROR_TREATMENTS) as ErrorTreatmentId[]) {
    const t = ERROR_TREATMENTS[id]
    describe(`treatment '${id}'`, () => {
      for (const mode of MODES) {
        const bg = () => tokenValue(BLOCKS[mode], t.paint.bg)
        const ink = () => tokenValue(BLOCKS[mode], t.paint.ink)
        const icon = () => tokenValue(BLOCKS[mode], t.paint.icon)

        it(`${mode}: body ink on the treated surface ≥ AA`, () => {
          expect(contrast(ink(), bg())).toBeGreaterThanOrEqual(AA)
        })
        it(`${mode}: alert glyph on the treated surface ≥ AA`, () => {
          expect(contrast(icon(), bg())).toBeGreaterThanOrEqual(AA)
        })
      }
    })
  }
})
