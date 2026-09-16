import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')


describe('the two canvas-painted accent texts use the emphasis shade', () => {
  it("the Memory Studio tab's active ink is the emphasis token", () => {
    const code = read('features/settings/MemoryPanel.tsx')
    expect(code).toMatch(/borderColor: 'var\(--color-primary\)', color: 'var\(--color-primary-emphasis\)'/)
    expect(code, 'the border may stay on the base accent — it is not text')
      .toMatch(/borderColor: 'var\(--color-primary\)'/)
  })

  it('the inbox-settings link uses the emphasis ink, in both copies of the panel', () => {
    for (const rel of ['features/inbox/InboxSettingsPanel.tsx', 'features/settings/InboxSettingsPanel.tsx']) {
      expect(read(rel), `${rel} link ink`).toMatch(/<TextLink[^>]*ink="emphasis"[^>]*>Open notification rules/)
    }
    expect(read('shared/ui/TextLink.tsx'), 'TextLink emphasis ink mapping')
      .toMatch(/emphasis:\s*'text-primary-emphasis'/)
  })

  it('neither site carries the old failing ink any more', () => {
    expect(read('features/inbox/InboxSettingsPanel.tsx'))
      .not.toMatch(/className="text-primary text-\[0\.8125rem\] hover:underline">Open notification rules/)
    expect(read('features/settings/MemoryPanel.tsx'))
      .not.toMatch(/borderColor: 'var\(--color-primary\)', color: 'var\(--color-primary\)'/)
  })

  it('the call sites carry the measurement, not just the token', () => {
    expect(read('features/settings/MemoryPanel.tsx')).toMatch(/4\.37:1/)
  })

  it('the scheme rail now measures the canvas, which is what makes this rule enforceable', () => {
    const rail = read('shared/theme/schemeContrast.test.ts')
    expect(rail).toMatch(/primary-emphasis as accent text on the CANVAS/)
    expect(rail, 'and it reads the LIGHT block, not the first mention of .light')
      .toMatch(/\\.light\\s\*\\\{/)
  })

  it('the emphasis token exists in light for every scheme', () => {
    const schemes = read('shared/theme/schemes.ts')
    const defs = [...schemes.matchAll(/primaryEmphasis:\s*\['#[0-9a-fA-F]{6}',\s*'#[0-9a-fA-F]{6}'\]/g)]
    expect(defs.length, 'schemes defining a light emphasis shade').toBeGreaterThanOrEqual(12)
  })
})


describe('accent chips on surface-high use the emphasis shade', () => {
  const SITES: [string, RegExp][] = [
    ['features/knowledge/KnowledgeListPage.tsx', /data-type="caption" className="[^"]*bg-surface-high px-1\.5 text-primary-emphasis/],
    ['shared/ui/Markdown.tsx', /bg-surface-high px-1\.5 align-baseline text-\[0\.8em\] text-primary-emphasis/],
    ['shared/ui/Markdown.tsx', /text-\[0\.85em\] font-mono text-primary-emphasis underline/],
    ['features/chat/PasteChip.tsx', /text-\[0\.92em\] text-primary-emphasis/],
    ['features/chat/PasteChip.tsx', /text-\[0\.85em\] text-primary-emphasis/],
    ['features/loops/LoopPlanReview.tsx', /data-type="caption" className="[^"]*text-primary-emphasis hover:bg-surface-high/],
    ['features/loops/LoopPlanReview.tsx', /data-type="body-s" className="[^"]*text-primary-emphasis hover:bg-surface-high/],
  ]
  for (const [rel, re] of SITES) {
    it(`${rel} pairs surface-high with the emphasis token (${re.source.slice(0, 34)}…)`, () => {
      expect(read(rel)).toMatch(re)
    })
  }

  it('no site pairs surface-high with the plain or alpha accent on the same element', () => {
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      readFileSync(abs, 'utf8').split('\n').forEach((line, i) => {
        for (const m of line.matchAll(/className=(?:\{`|")([^"`]*)(?:`\}|")/g)) {
          const cls = m[1]
          if (!/(?:^|\s)bg-surface-high(?:\s|$)/.test(cls)) continue
          if (/(?:^|\s)text-primary(?:\/\d+)?(?:\s|$)/.test(cls)) offenders.push(`${abs.slice(SRC.length + 1)}:${i + 1}`)
        }
      })
    }
    expect(offenders, `these cannot reach AA on this ground:\n${offenders.join('\n')}`).toEqual([])
  })

  it('the scheme rail carries the number for every scheme, not just the default', () => {
    const rail = read('shared/theme/schemeContrast.test.ts')
    expect(rail).toMatch(/primary-emphasis as accent text on SURFACE-HIGH/)
    expect(rail, 'both modes').toMatch(/contrast\(emphasis\.dark, HIGH_DARK\)/)
  })
})


describe('a dashboard row action uses the emphasis shade', () => {
  it("RowAction's primary tone is the emphasis ink", () => {
    const code = read('features/dashboard/widgets/kit.tsx')
    expect(code).toMatch(/primary: 'text-primary-emphasis hover:bg-primary-container\/40'/)
    expect(code, 'the failing ink must be gone').not.toMatch(/primary: 'text-primary hover:bg-primary-container/)
  })

  it('its sibling tones are untouched, because they already pass on that ground', () => {
    const code = read('features/dashboard/widgets/kit.tsx')
    expect(code).toMatch(/ok: 'text-ok hover:bg-ok\/15'/)
    expect(code).toMatch(/danger: 'text-danger hover:bg-danger\/15'/)
    expect(code).toMatch(/default: 'text-on-surface-var hover:bg-surface-highest hover:text-on-surface'/)
  })

  it('the call sites it reaches are the four dashboard row actions', () => {
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    const sites = walk(join(SRC, 'features/dashboard')).flatMap((abs) =>
      [...readFileSync(abs, 'utf8').matchAll(/<RowAction tone="primary"/g)].map(() => abs.slice(SRC.length + 1)))
    expect(sites.length, 'RowAction tone="primary" call sites on the dashboard').toBeGreaterThanOrEqual(4)
  })

  it('the scheme rail carries the fourth ground for every scheme', () => {
    const rail = read('shared/theme/schemeContrast.test.ts')
    expect(rail).toMatch(/primary-emphasis as accent text on SURFACE-LOW/)
    expect(rail, 'both modes').toMatch(/contrast\(emphasis\.dark, LOW_DARK\)/)
  })
})


describe('a tone-registry ink painted on the canvas uses the emphasis shade', () => {
  it('the breadcrumb type segment inks through the canvas helper, not the raw tone', () => {
    const code = read('features/knowledge/KnowledgeDetailPage.tsx')
    expect(code).toMatch(/style=\{\{ color: canvasInk\(tm\.tone\) \}\}/)
    expect(code, 'the failing ink must be gone from the segment')
      .not.toMatch(/whitespace-nowrap" style=\{\{ color: tm\.tone \}\}/)
  })

  it('the helper remaps the one failing tone and is the identity for the rest', () => {
    const code = read('features/knowledge/KnowledgeDetailPage.tsx')
    const m = code.match(/const canvasInk = \(tone: string\) => \((.+)\)\n/)
    expect(m, 'canvasInk must exist as a single-expression helper').toBeTruthy()
    const canvasInk = new Function('tone', `return (${m![1]})`) as (t: string) => string
    expect(canvasInk('var(--color-primary)')).toBe('var(--color-primary-emphasis)')
    for (const t of ['var(--color-info)', 'var(--color-ok)', 'var(--color-warn)', 'var(--color-danger)'])
      expect(canvasInk(t), `${t} passes on the canvas and must be left alone`).toBe(t)
  })

  it('the mapping is not vacuous — the registry still declares the tone it remaps', () => {
    const meta = read('features/knowledge/knowledgeMeta.ts')
    const primaryKinds = [...meta.matchAll(/key: '(\w+)'[^}]*tone: 'var\(--color-primary\)'/g)].map((x) => x[1])
    expect(primaryKinds, 'kinds whose tone this helper remaps').toEqual(['note', 'fleeting', 'journal'])
  })

  it('the shared registry is untouched, so the icons keep the base accent', () => {
    expect(read('features/knowledge/knowledgeMeta.ts'))
      .toMatch(/key: 'note', label: 'Note', icon: StickyNote, tone: 'var\(--color-primary\)'/)
  })

  it('the call site carries the measurement, not just the token', () => {
    expect(read('features/knowledge/KnowledgeDetailPage.tsx')).toMatch(/4\.37:1/)
  })

  it('this ground is already scheme-covered, which is what makes the remap safe in all 12', () => {
    expect(read('shared/theme/schemeContrast.test.ts')).toMatch(/primary-emphasis as accent text on the CANVAS/)
  })
})


describe('the first-run overlay inks its links by their ground', () => {
  const LINK = read('shared/ui/TextLink.tsx')
  const ONB = read('app/shell/Onboarding.tsx')

  it('TextLink exposes the ink as a prop, mapped to the shipped emphasis token', () => {
    expect(LINK).toMatch(/type Ink = 'primary' \| 'emphasis'/)
    expect(LINK).toMatch(/primary: 'text-primary',/)
    expect(LINK).toMatch(/emphasis: 'text-primary-emphasis',/)
    expect(LINK, 'and the class comes from the map, not a hardcoded ink').toMatch(/const cls = cx\(\s*INK\[ink\],/)
  })

  it('the default stays `primary`, so no compliant link moved', () => {
    expect(LINK).toMatch(/ink = 'primary'/)
  })

  it('the canvas-painted skip link takes the emphasis ink', () => {
    expect(ONB).toMatch(/<TextLink size="sm" ink="emphasis" onClick=\{skipSetup\}>/)
  })

  it('the surface-high Pointer link takes it too', () => {
    expect(ONB).toMatch(/<TextLink size="sm" ink="emphasis" onClick=\{\(\) => onExitTo\('inbox'\)\}>Open the Inbox instead<\/TextLink>/)
  })

  it('neither onboarding link carries the failing default any more', () => {
    expect(ONB).not.toMatch(/<TextLink size="sm" onClick=\{skipSetup\}>/)
    expect(ONB).not.toMatch(/<TextLink size="sm" onClick=\{\(\) => onExitTo\('inbox'\)\}>/)
  })

  it('the links INSIDE the step card keep the base ink — they measured 4.83 and pass', () => {
    const essentials = read('features/onboarding/EssentialsStep.tsx')
    const plain = [...essentials.matchAll(/<TextLink(?![^>]*\bink=)/g)]
    expect(plain.length, 'EssentialsStep links still on the default ink').toBeGreaterThanOrEqual(3)
  })

  it('both call sites carry the measurement, not just the token', () => {
    expect(ONB).toMatch(/4\.37:1/)
    expect(ONB).toMatch(/4\.26:1/)
  })

  it('the emphasis shade exists in light for every scheme, on BOTH grounds this cycle touched', () => {
    const rail = read('shared/theme/schemeContrast.test.ts')
    expect(rail).toMatch(/primary-emphasis as accent text on the CANVAS/)
    const schemes = read('shared/theme/schemes.ts')
    const defs = [...schemes.matchAll(/primaryEmphasis:\s*\['#[0-9a-fA-F]{6}',\s*'#[0-9a-fA-F]{6}'\]/g)]
    expect(defs.length, 'schemes defining a light emphasis shade').toBeGreaterThanOrEqual(12)
  })
})


describe('the voice panel manage-links are inked for the canvas', () => {
  const VOICE = read('features/settings/VoicePanel.tsx')

  it('both manage-links take the emphasis ink', () => {
    const emph = [...VOICE.matchAll(/<TextLink onClick=\{\(\) => go\('(models|providers)'\)\}[^>]*ink="emphasis"/g)]
    expect(emph.length, 'manage-links on the emphasis shade').toBe(2)
  })

  it('neither carries the failing default any more', () => {
    expect(VOICE).not.toMatch(/<TextLink onClick=\{\(\) => go\('models'\)\} icon=\{ArrowRight\} iconPosition="trailing" size="xs">/)
    expect(VOICE).not.toMatch(/<TextLink onClick=\{\(\) => go\('providers'\)\} icon=\{ArrowRight\} iconPosition="trailing" size="xs">/)
  })

  it('the call site carries the measurement, not just the token', () => {
    expect(VOICE).toMatch(/4\.37:1/)
  })

  it("the panel's OTHER link keeps the base ink — it is on a surface and passes", () => {
    expect(VOICE).toMatch(/<TextLink size="xs" onClick=\{async \(\) => \{/)
  })

  it('the primitive default is still `primary`, so the 20 passing links did not move', () => {
    expect(read('shared/ui/TextLink.tsx')).toMatch(/ink = 'primary'/)
  })
})
