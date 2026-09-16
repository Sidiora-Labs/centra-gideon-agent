
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'

import { Morph, MORPH_FAMILY, familySpring } from './index'
import { runtime } from '../../theme/runtime'
import { physics } from '../../theme/motion'

const DEFAULT_EXPRESSIVENESS = runtime.expressiveness
afterEach(() => { runtime.expressiveness = DEFAULT_EXPRESSIVENESS })

const read = (name: string) => readFileSync(join(process.cwd(), "src", name), 'utf8')
const root = () => document.querySelector<HTMLElement>('[data-morph]')!

describe('Morph — the shared-element branch', () => {
  it('takes the shared branch and keeps the layout classes it was handed', () => {
    render(<Morph id="artifact-x" className="grid"><p>card</p></Morph>)
    expect(root()).toHaveAttribute('data-morph', 'shared')
    expect(root().className).toBe('grid')
    expect(screen.getByText('card')).toBeInTheDocument()
  })

  it('still passes clicks through to its children', () => {
    const onPoke = vi.fn()
    render(<Morph id="artifact-x"><button type="button" onClick={onPoke}>open</button></Morph>)
    screen.getByRole('button', { name: 'open' }).click()
    expect(onPoke).toHaveBeenCalledTimes(1)
  })

  it('declares layoutId and NOT layout — the property no DOM assertion can reach', () => {
    const src = read('shared/ui/motion/Morph.tsx')
    expect(src).toMatch(/layoutId=\{id\}/)
    expect(src, 'Morph must not add the `layout` prop — see its docblock on measuring cost')
      .not.toMatch(/<motion\.div[^>]*\slayout(\s|=|\})/)
  })
})

describe('the morph transition', () => {
  it('is the fluid preset, stiffened, so it tracks BOTH personality knobs', () => {
    const t = familySpring(MORPH_FAMILY.flight) as { type?: string; damping?: number; stiffness?: number }
    const fluid = physics.fluid as { damping?: number }
    expect(t.type).toBe('spring')
    expect(t.damping).toBe(fluid.damping)
  })

  it('scales its stiffness with expressiveness, bounded by the named constants', () => {
    const stiffness = () => (familySpring(MORPH_FAMILY.flight) as { stiffness: number }).stiffness
    runtime.expressiveness = 1
    const bold = stiffness()
    runtime.expressiveness = 0
    const refined = stiffness()

    expect(bold).toBe(MORPH_FAMILY.flight + MORPH_FAMILY.stiffnessBonus)
    expect(refined).toBe(MORPH_FAMILY.flight + MORPH_FAMILY.stiffnessBonus * MORPH_FAMILY.floor)
    expect(refined).toBeGreaterThan(MORPH_FAMILY.flight)
    expect(bold).toBeGreaterThan(refined)
  })
})

describe('the one real morph is wired at BOTH ends', () => {
  const grid = read('features/artifacts/ArtifactGrid.tsx')
  const section = read('features/artifacts/ArtifactsSection.tsx')

  it('the library card is the opening end', () => {
    expect(grid).toMatch(/<Morph[^>]*id=\{`artifact-\$\{a\.slug\}`\}/)
    expect(grid).toMatch(/from '\.\.\/\.\.\/shared\/ui\/motion'/)
  })

  it('the full-page viewer is the closing end, on the SAME id', () => {
    expect(section).toMatch(/<Morph[^>]*id=\{`artifact-\$\{slug\}`\}/)
  })

  it('the two ends are never mounted at once — the precondition for a morph at all', () => {
    expect(section).toMatch(/const slug = \(sub \|\| ''\)\.split\('\/'\)\[0\] \|\| ''/)
    expect(section).toMatch(/\{slug \? \(/)
    expect(section).toMatch(/const \[artifacts, setArtifacts\] = useState<Artifact\[\]>\(\[\]\)/)
  })
})
