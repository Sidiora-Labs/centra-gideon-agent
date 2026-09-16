import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SCAN_FINDINGS_SHOWN, hiddenFindingsNote } from './scanFindings'
import { ScanReport } from '../../features/apps/installConsent'


const SRC = join(process.cwd(), "src")
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

const CONSENT = ['features/apps/installConsent.tsx', 'features/skills/MarketplaceDetail.tsx']

describe('the residue sentence', () => {
  it('says nothing when nothing is hidden', () => {
    expect(hiddenFindingsNote(0)).toBeNull()
    expect(hiddenFindingsNote(SCAN_FINDINGS_SHOWN), 'exactly at the cap hides nothing').toBeNull()
    expect(hiddenFindingsNote(SCAN_FINDINGS_SHOWN - 1)).toBeNull()
  })

  it('counts the residue, not the total', () => {
    expect(hiddenFindingsNote(SCAN_FINDINGS_SHOWN + 6)).toBe('+6 more findings not shown')
    expect(hiddenFindingsNote(SCAN_FINDINGS_SHOWN + 1), 'singular at one').toBe('+1 more finding not shown')
  })
})

describe('both consent surfaces disclose their cap', () => {
  it('neither hardcodes its own limit any more', () => {
    for (const rel of CONSENT) {
      const src = read(rel)
      expect(src, `${rel} must slice by the shared constant`).toMatch(/\.slice\(0, SCAN_FINDINGS_SHOWN\)/)
      expect(src, `${rel} must not re-choose the limit`).not.toMatch(/findings\.slice\(\s*0\s*,\s*\d/)
      expect(src, `${rel} must import the shared rules`).toMatch(
        /import \{[^}]*\bSCAN_FINDINGS_SHOWN\b[^}]*\bhiddenFindingsNote\b[^}]*\} from '(\.\.\/)+lib\/scanFindings'/,
      )
    }
  })

  it('each renders the residue, gated on there being one', () => {
    for (const rel of CONSENT) {
      const src = read(rel)
      expect(src, `${rel} must render the note`).toMatch(/hiddenFindingsNote\([\w.?]+\.findings\.length\)/)
      expect((src.match(/hiddenFindingsNote\(/g) ?? []).length,
        `${rel}: once to decide, once to render`).toBeGreaterThanOrEqual(2)
    }
  })

  it('the app-install report states the total, since nothing else on that screen did', () => {
    const src = read('features/apps/installConsent.tsx')
    expect(src, 'the verdict line carries the count').toMatch(
      /Security scan: \{v\}[\s\S]{0,140}scan\.findings\.length\} finding\$\{/,
    )
  })

  it('the skill surface keeps the total it already had', () => {
    const src = read('features/skills/MarketplaceDetail.tsx')
    expect(src).toMatch(/Security scan flagged \$\{blocked\.scan\?\.findings\?\.length \?\? 0\} warning/)
  })

  it('no OTHER surface lists scan findings without the note — the census', () => {
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n: string) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n) ? [p] : []
      })
    const renderers = walk(SRC)
      .map((abs) => ({ rel: abs.replace(SRC + '/', ''), src: strip(readFileSync(abs, 'utf8')) }))
      .filter(({ src }) => /scan\??\.findings[\s\S]{0,40}\.slice\(\s*0/.test(src) && src.includes('.map('))
    expect(renderers.length, 'the census must find the consent surfaces').toBeGreaterThanOrEqual(2)
    const silent = renderers.filter(({ src }) => !src.includes('hiddenFindingsNote')).map((r) => r.rel)
    expect(silent, 'a truncated findings list must say how many it hides').toEqual([])
  })
})

describe('what the consent screen actually reads', () => {
  const scanOf = (n: number) => ({
    verdict: 'warning' as const,
    findings: Array.from({ length: n }, (_, i) => ({
      rule: `rule_${i}`, severity: 'warning' as const, path: `f${i}.py`, evidence: '',
    })),
  })

  it('a 14-finding scan says 14, lists 8, and admits the other 6', () => {
    render(<ScanReport scan={scanOf(14) as never} />)
    expect(screen.getByText(/Security scan: warning/)).toBeTruthy()
    expect(screen.getByText(/14 findings/), 'the total').toBeTruthy()
    expect(screen.getAllByText(/^rule_/).length, 'eight listed').toBe(SCAN_FINDINGS_SHOWN)
    expect(screen.getByText('+6 more findings not shown'), 'and the residue').toBeTruthy()
  })

  it('a scan inside the cap says neither a residue nor a lie', () => {
    render(<ScanReport scan={scanOf(3) as never} />)
    expect(screen.getByText(/3 findings/)).toBeTruthy()
    expect(screen.queryByText(/more findings? not shown/), 'nothing is hidden').toBeNull()
  })

  it('a clean scan carries no count at all — there is nothing to count', () => {
    render(<ScanReport scan={{ verdict: 'clean', findings: [] } as never} />)
    expect(screen.getByText(/Security scan: clean/)).toBeTruthy()
    expect(screen.queryByText(/finding/), 'no "0 findings" noise on a clean install').toBeNull()
  })
})
