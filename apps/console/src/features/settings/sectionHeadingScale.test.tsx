import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Boxes } from 'lucide-react'
import { Section } from './settingsUI'


describe('Section carries what the outliers had opted out for', () => {
  it('renders the title at the panel-section scale, once', () => {
    const { container } = render(<Section title="Backdrop & motion">x</Section>)
    const h = container.querySelector('h2')!
    expect(h.getAttribute('data-type')).toBe('title-m')
    expect(h.textContent).toBe('Backdrop & motion')
  })

  it('takes a leading icon without changing the heading level', () => {
    const { container } = render(<Section title="Typography & scale" icon={Boxes}>x</Section>)
    expect(container.querySelector('h2 svg'), 'the glyph belongs inside the heading row').not.toBeNull()
    expect(container.querySelectorAll('h3').length, 'still an h2 — the panel title is the h1').toBe(0)
  })

  it('takes a right-hand control beside the title', () => {
    render(<Section title="Color scheme" right={<button type="button">Dark</button>}>x</Section>)
    expect(screen.getByRole('button', { name: 'Dark' })).toBeTruthy()
  })

  it('takes a hint with live content, not just a string', () => {
    render(<Section title="Live logs" hint={<span>Streaming · <b>12</b> shown</span>}>x</Section>)
    expect(screen.getByText('12')).toBeTruthy()
  })

  it('still renders a bare section with no header at all', () => {
    const { container } = render(<Section>only children</Section>)
    expect(container.querySelector('h2')).toBeNull()
    expect(container.textContent).toBe('only children')
  })
})

describe('no settings panel hand-rolls a section title any more', () => {
  const DIR = join(process.cwd(), "src/features/settings")
  const offenders = readdirSync(DIR)
    .filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))
    .flatMap((n) => {
      const src = readFileSync(join(DIR, n), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      return [...src.matchAll(/<h2[^>]*className="([^"]*)"/g)]
        .filter((m) => /text-\[(0\.9375|1\.0625|1(\.\d+)?)rem\]/.test(m[1]))
        .map(() => n)
    })

  it('leaves none', () => {
    expect([...new Set(offenders)].filter((n) => n !== 'settingsUI.tsx'), 'a panel writing its own section title drifts from the other 23').toEqual([])
  })

  it('and Section is genuinely the shared owner (not vacuously green)', () => {
    const uses = readdirSync(DIR)
      .filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))
      .reduce((sum, n) => sum + (readFileSync(join(DIR, n), 'utf8').match(/<Section\b/g) ?? []).length, 0)
    expect(uses, 'the primitive must actually be in use').toBeGreaterThanOrEqual(76)
  })
})

describe('a panel that names its sections names ALL of them', () => {

  const DIR = join(process.cwd(), "src/features/settings")
  const clean = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  function sectionTags(src: string): string[] {
    const out: string[] = []
    for (const m of src.matchAll(/<Section\b/g)) {
      let depth = 0
      for (let i = m.index! + m[0].length; i < src.length; i++) {
        const c = src[i]
        if (c === '{') depth++
        else if (c === '}') depth--
        else if (c === '>' && depth === 0) { out.push(src.slice(m.index!, i + 1)); break }
      }
    }
    return out
  }

  const panels = readdirSync(DIR)
    .filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) && n !== 'settingsUI.tsx')
    .map((n) => {
      const tags = sectionTags(clean(readFileSync(join(DIR, n), 'utf8')))
      return { n, titled: tags.filter((t) => /\stitle=/.test(t)).length, bare: tags.length - tags.filter((t) => /\stitle=/.test(t)).length }
    })
    .filter((p) => p.titled + p.bare > 0)

  const H1_OWNS_ITS_ONLY_GROUP = new Set(['SearchPanel.tsx'])

  it('no panel mixes titled sections with untitled ones', () => {
    expect(panels.length, 'vacuity floor — the scan must resolve the panels').toBeGreaterThanOrEqual(25)
    expect(panels.reduce((s, p) => s + p.titled, 0), 'and find the titled sections').toBeGreaterThanOrEqual(50)
    const mixed = panels.filter((p) => p.titled > 0 && p.bare > 0).map((p) => `${p.n} (${p.titled} titled, ${p.bare} bare)`)
    expect(mixed, `these attribute content to the wrong group, or to none:\n${mixed.join('\n')}`).toEqual([])
  })

  it('the h1-owns-it exception is exactly one panel, and still has no titled section', () => {
    for (const n of H1_OWNS_ITS_ONLY_GROUP) {
      const p = panels.find((x) => x.n === n)
      expect(p, `${n} must still be in scope`).toBeTruthy()
      expect(p!.bare, `${n} is the single-group case`).toBeGreaterThan(0)
      expect(p!.titled, `${n} gained a titled section — now title its bare one too`).toBe(0)
    }
    const bareOnly = panels.filter((p) => p.titled === 0 && p.bare > 0).map((p) => p.n)
    expect(bareOnly.sort(), 'a new bare-only panel needs a verdict here, not silence')
      .toEqual([...H1_OWNS_ITS_ONLY_GROUP].sort())
  })

  it('the four fixed panels name their primary group', () => {
    const titleOf = (n: string) => clean(readFileSync(join(DIR, n), 'utf8'))
    expect(titleOf('DoctorPanel.tsx')).toMatch(/<Section title="Subsystem probes">/)
    expect(titleOf('ModelsPanel.tsx')).toMatch(/<Section title="Model bindings" hint=/)
    expect(titleOf('RoutingPanel.tsx')).toMatch(/<Section title="Model efficiency">/)
    expect(titleOf('AppsPanel.tsx')).toMatch(/<Section title="Installed app settings" hint=/)
  })

  it('a section keeps its title in its FAILURE branch, not only its success one', () => {
    const src = clean(readFileSync(join(DIR, 'RoutingPanel.tsx'), 'utf8'))
    const policy = src.slice(src.indexOf('function RoutingPolicySection'))
    const tags = sectionTags(policy)
    expect(tags.length, 'the early return plus the main render').toBeGreaterThanOrEqual(2)
    for (const t of tags) expect(t, 'every branch of this section names it').toMatch(/title="Routing policy"/)
  })
})

describe('the panels this rail cannot speak for', () => {
  const DIR = join(process.cwd(), "src/features/settings")
  const NO_SECTIONS = new Set([
    'ArchivePanel.tsx',
    'AuditPanel.tsx',
    'MultiInstanceCard.tsx',
    'ProviderCard.tsx',
    'ProviderConfigForm.tsx',
  ])

  it('is exactly this set — a new sectionless panel needs a verdict, not silence', () => {
    const found = readdirSync(DIR)
      .filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) && n !== 'settingsUI.tsx')
      .filter((n) => {
        const src = readFileSync(join(DIR, n), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
        return src.includes("from './settingsUI'") && !src.includes('<Section')
      })
    expect(found.length, 'vacuity floor — the scan must resolve files').toBeGreaterThan(0)
    expect(found.sort(), 'add it to NO_SECTIONS with a reason, or give it sections')
      .toEqual([...NO_SECTIONS].sort())
  })
})

describe('a section glyph is muted unless it marks something live', () => {

  const DIR = join(process.cwd(), "src/features/settings")
  const clean = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  function iconSections() {
    const out: { file: string; tag: string; muted: boolean }[] = []
    for (const n of readdirSync(DIR)) {
      if (!/\.tsx$/.test(n) || /\.(test|doc)\.tsx$/.test(n)) continue
      const src = clean(readFileSync(join(DIR, n), 'utf8'))
      for (const m of src.matchAll(/<Section\b/g)) {
        let depth = 0
        for (let i = m.index! + m[0].length; i < src.length; i++) {
          const c = src[i]
          if (c === '{') depth++
          else if (c === '}') depth--
          else if (c === '>' && depth === 0) {
            const tag = src.slice(m.index!, i + 1)
            if (/\sicon=/.test(tag)) out.push({ file: n, tag, muted: /iconTone="muted"/.test(tag) })
            break
          }
        }
      }
    }
    return out
  }

  const CORAL_IS_MEANT_HERE = new Set(['DesignPanel.tsx'])

  it('every decorative section glyph is muted', () => {
    const sections = iconSections()
    expect(sections.length, 'vacuity floor — the scan must find the icon-passing sections')
      .toBeGreaterThanOrEqual(6)
    const coral = sections.filter((s) => !s.muted).map((s) => s.file)
    expect([...new Set(coral)].sort(), 'coral means "alive/active/primary" — not a category glyph')
      .toEqual([...CORAL_IS_MEANT_HERE].sort())
  })

  it('the three that were muted stay muted', () => {
    const sections = iconSections()
    const mutedIn = (file: string) => sections.filter((s) => s.file === file && s.muted).length
    expect(mutedIn('AlwaysOnConventions.tsx'), 'Always-on skills + Project instructions').toBe(2)
    expect(mutedIn('DurabilityPanel.tsx'), 'Time travel').toBe(1)
    expect(mutedIn('ProvidersPanel.tsx'), 'the nine entity glyphs, muted by the earlier cycle').toBe(1)
  })

  it('the primitive still defaults to primary, which is why the list above is needed', () => {
    const src = readFileSync(join(DIR, 'settingsUI.tsx'), 'utf8')
    expect(src).toMatch(/iconTone = 'primary'/)
    expect(src, 'and the two tones must still resolve to different inks')
      .toMatch(/iconTone === 'muted' \? 'text-on-surface-low' : 'text-primary'/)
  })
})
