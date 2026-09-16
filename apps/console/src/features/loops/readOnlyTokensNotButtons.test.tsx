import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TokensView } from './DesignCockpitPage'


const FIXTURE = {
  resolved: {
    radius: { sm: '0.25rem', lg: '0.75rem', comment: 'ignored' },
    typography: { family: { sans: 'Inter, sans-serif' }, size: {}, weight: {} },
    spacing: {}, shadow: {}, color: { semantic: {}, primitive: {} },
  },
} as unknown as Parameters<typeof TokensView>[0]['tokens']

const SCHEME = 'coral' as unknown as Parameters<typeof TokensView>[0]['scheme']

describe('read-only design tokens are not pretending to be buttons', () => {
  it('renders no radius/family button when there is nothing to press', () => {
    render(<TokensView tokens={FIXTURE} scheme={SCHEME} readOnly />)
    expect(screen.queryByTitle(/^Override radius\./), 'read-only must not offer an override').toBeNull()
    expect(screen.queryByTitle(/^Override typography\.family\./)).toBeNull()
  })

  it('puts the value on screen instead of hiding it in a title', () => {
    render(<TokensView tokens={FIXTURE} scheme={SCHEME} readOnly />)
    expect(screen.getByText('lg · 0.75rem'), 'the value must be readable without a hover').toBeTruthy()
  })

  it('is still a real button when overriding IS possible', () => {
    render(<TokensView tokens={FIXTURE} scheme={SCHEME} onOverride={() => {}} />)
    const b = screen.getByTitle('Override radius.lg (now 0.75rem)')
    expect(b.tagName).toBe('BUTTON')
    expect((b as HTMLButtonElement).disabled, 'an available action is not disabled').toBe(false)
  })

  it('does not print the value twice in the editable view', () => {
    render(<TokensView tokens={FIXTURE} scheme={SCHEME} onOverride={() => {}} />)
    expect(screen.queryByText('lg · 0.75rem')).toBeNull()
  })
})

describe('the Suggest-more gate keeps its tab stop', () => {
  const src = readFileSync(join(process.cwd(), "src/features/loops/LoopPlanReview.tsx"), 'utf8')

  it('names the gate instead of inlining it twice', () => {
    expect(src).toMatch(/const tooShort = goal\.trim\(\)\.length < 20/)
  })

  it('keeps the native attribute for busy and only that', () => {
    expect(src).toMatch(/disabled=\{busy\}/)
    expect(src).toMatch(/aria-disabled=\{tooShort \|\| undefined\}/)
  })

  it('says why, and suppresses the click it can no longer refuse natively', () => {
    expect(src).toMatch(/title=\{tooShort \? 'Describe the goal in a bit more detail first' : undefined\}/)
    expect(src).toMatch(/onClick=\{tooShort \? undefined : suggest\}/)
  })

  it('carries the dimming on BOTH selectors, or the soft-off state looks enabled', () => {
    expect(src).toMatch(/disabled:opacity-40 aria-disabled:opacity-40/)
  })
})
