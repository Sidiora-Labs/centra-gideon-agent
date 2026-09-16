import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { RowGroup } from './settingsUI'


const SETTINGS = join(process.cwd(), "src/features/settings")
const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const panels = () => readdirSync(SETTINGS).filter((f) => /\.tsx$/.test(f) && !/\.test\.tsx$/.test(f))

describe('RowGroup is the one settings row-group surface', () => {
  it('renders a container-tone lg-radius slab padded from the spacing scale', () => {
    const { container } = render(<RowGroup><span>row</span></RowGroup>)
    const el = container.firstElementChild as HTMLElement
    expect(el, 'RowGroup must render an element').toBeTruthy()
    const cls = el.className.split(/\s+/)
    expect(cls, 'one tonal step: bg-surface-container').toContain('bg-surface-container')
    expect(cls, 'rounded-lg').toContain('rounded-lg')
    expect(cls, 'horizontal padding from the scale, not Tailwind px-4').toContain('px-l')
    expect(cls, 'vertical padding from the scale, not Tailwind py-1').toContain('py-xs')
    expect(cls, 'px-4 is the frozen spelling this replaced').not.toContain('px-4')
    expect(cls, 'py-1 is the frozen spelling this replaced').not.toContain('py-1')
    expect(el.textContent).toBe('row')
  })

  it('px-l/py-xs really are 16px/4px scaled by --space-scale', () => {
    const tokens = read('shared/theme/tokens.css')
    expect(tokens, '--spacing-l must be 16px * --space-scale (== Tailwind px-4 at scale 1)')
      .toMatch(/--spacing-l:\s*calc\(16px\s*\*\s*var\(--space-scale\)\)/)
    expect(tokens, '--spacing-xs must be 4px * --space-scale (== Tailwind py-1 at scale 1)')
      .toMatch(/--spacing-xs:\s*calc\(4px\s*\*\s*var\(--space-scale\)\)/)
    expect(tokens, 'dense re-scales --space-scale').toMatch(/--space-scale:\s*0\.8/)
    expect(tokens, 'cli re-scales --space-scale').toMatch(/--space-scale:\s*0\.68/)
  })

  it('no shipped markup still hand-rolls the slab', () => {
    const offenders: string[] = []
    for (const f of panels()) {
      if (/rounded-lg bg-surface-container px-4 py-1/.test(code(readFileSync(join(SETTINGS, f), 'utf8')))) {
        offenders.push(`pages/settings/${f}`)
      }
    }
    if (/rounded-lg bg-surface-container px-4 py-1/.test(code(read('shared/ui/ListScaffold.tsx')))) {
      offenders.push('shared/ui/ListScaffold.tsx')
    }
    expect(offenders, `hand-rolled row-group slab in: ${offenders.join(', ')}`).toEqual([])
  })

  it('the 42 exact sites really did adopt it', () => {
    const uses = panels()
      .map((f) => (readFileSync(join(SETTINGS, f), 'utf8').match(/<RowGroup[\s>]/g) ?? []).length)
      .reduce((a, b) => a + b, 0)
    expect(uses, 'settings must carry at least the 46 adoptions this change made').toBeGreaterThanOrEqual(46)
  })

  it('each of the 4 near-miss sites is now the primitive, at the majority padding', () => {
    const guardrails = read('features/settings/GuardrailsPanel.tsx')
    expect(guardrails.match(/<RowGroup>\s*<Field label="Scan mode"/),
      'GuardrailsPanel outbound-scan group (was py-3, one Field)').toBeTruthy()

    const agent = read('features/settings/AgentDefaultsPanel.tsx')
    expect(agent.match(/<RowGroup>\s*<Row label="Default agent"/),
      'AgentDefaultsPanel default-agent group (was py-2)').toBeTruthy()

    const packs = read('features/settings/PacksPanel.tsx')
    expect(packs.match(/<RowGroup key=\{p\.name\}>\s*<Row label=/),
      'PacksPanel store row (was py-3, and its `key` is why a regex sweep missed it)').toBeTruthy()
    expect(packs.match(/<RowGroup>\s*<Row label=\{`\$\{pack\.name\}/),
      'PacksPanel installed-pack row (was py-3)').toBeTruthy()
  })

  it('no panel re-declares a private copy', () => {
    const definers = panels()
      .filter((f) => f !== 'settingsUI.tsx')
      .filter((f) => /function RowGroup\b|const RowGroup\b/.test(readFileSync(join(SETTINGS, f), 'utf8')))
    expect(definers, `private RowGroup in: ${definers.join(', ')}`).toEqual([])
  })

  it('FormSkeleton stays padding-identical to the loaded group', () => {
    const scaffold = read('shared/ui/ListScaffold.tsx')
    const slab = scaffold.match(/<Surface tone="container" radius="lg" className="[^"]*"/)?.[0] ?? ''
    expect(slab, 'FormSkeleton must render a container Surface').toContain('<Surface')
    expect(slab, 'same horizontal padding as RowGroup').toContain('px-l')
    expect(slab, 'same vertical padding as RowGroup').toContain('py-xs')
    const rowGroup = read('features/settings/settingsUI.tsx')
      .match(/export function RowGroup[\s\S]*?\n\}/)?.[0] ?? ''
    const pad = rowGroup.match(/className="([^"]*)"/)?.[1]
    expect(pad, "RowGroup's padding must be readable").toBe('px-l py-xs')
    expect(slab, `FormSkeleton must carry RowGroup's exact padding (${pad})`).toContain(`className="${pad}"`)
  })

  it('the competing py-m row-group padding does not spread', () => {
    const count = panels()
      .map((f) => (readFileSync(join(SETTINGS, f), 'utf8').match(/px-l py-m/g) ?? []).length)
      .reduce((a, b) => a + b, 0)
    expect(count, 'a new row group belongs in RowGroup, not a fresh px-l py-m slab').toBeLessThanOrEqual(7)
  })
})
