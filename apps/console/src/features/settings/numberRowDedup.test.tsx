import { describe, expect, it, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { NumberRow } from './settingsUI'


const SETTINGS = join(process.cwd(), "src/features/settings")

const MIGRATED = ['SourcesPanel', 'AmbientPanel']
const OTHER_CONTRACT = ['GuardrailsPanel', 'DurabilityPanel', 'ChatPanel', 'AgentDefaultsPanel']

describe('the cfg-driven NumberRow lives in settingsUI', () => {
  it('neither migrated panel declares a private copy', () => {
    const definers = MIGRATED
      .filter((f) => /function NumberRow\b/.test(readFileSync(join(SETTINGS, `${f}.tsx`), 'utf8')))
    expect(definers, `private NumberRow in: ${definers.join(', ')}`).toEqual([])
  })

  it('neither migrated panel keeps a private num() coercion helper', () => {
    const leftovers = MIGRATED
      .filter((f) => /function num\(/.test(readFileSync(join(SETTINGS, `${f}.tsx`), 'utf8')))
    expect(leftovers, `orphaned num() in: ${leftovers.join(', ')}`).toEqual([])
  })

  it('both migrated panels import it', () => {
    for (const f of MIGRATED) {
      expect(readFileSync(join(SETTINGS, `${f}.tsx`), 'utf8'), `${f} should import NumberRow`)
        .toMatch(/import \{[^}]*\bNumberRow\b[^}]*\} from '\.\/settingsUI'/)
    }
  })

  it('the other-contract panels are left alone', () => {
    for (const f of OTHER_CONTRACT) {
      expect(readFileSync(join(SETTINGS, `${f}.tsx`), 'utf8'),
        `${f} declares NumberRow on its own contract and should stay that way`)
        .toMatch(/function NumberRow\b/)
    }
  })
})

describe('NumberRow behaviour', () => {
  const rowFor = (cfg: Record<string, unknown>, patch = vi.fn()) => ({
    patch,
    ...render(<NumberRow label="Max tiles" cfg={cfg} field="max" min={1} max={48} patch={patch as never} />),
  })

  it('shows the configured value', () => {
    const { container } = rowFor({ max: 12 })
    expect(container.querySelector('input')?.value).toBe('12')
  })

  it('falls back to min when the key is missing', () => {
    const { container } = rowFor({})
    expect(container.querySelector('input')?.value).toBe('1')
  })

  it('falls back to min for a non-numeric value rather than showing NaN', () => {
    const { container } = rowFor({ max: 'abc' })
    expect(container.querySelector('input')?.value).toBe('1')
  })

  it('keeps a legitimate zero instead of treating it as absent', () => {
    const { container } = render(
      <NumberRow label="Layers" cfg={{ n: 0 }} field="n" min={0} max={2} patch={vi.fn() as never} />)
    expect(container.querySelector('input')?.value).toBe('0')
  })

  it('patches the field with the committed number and a flash callback', () => {
    const patch = vi.fn()
    const { container } = rowFor({ max: 12 }, patch)
    const input = container.querySelector('input')!
    fireEvent.change(input, { target: { value: '20' } })
    fireEvent.blur(input)
    expect(patch).toHaveBeenCalledWith('max', 20, expect.any(Function), 'Max tiles')
  })

  it('clamps to max on commit (inherited from NumberField)', () => {
    const patch = vi.fn()
    const { container } = rowFor({ max: 12 }, patch)
    const input = container.querySelector('input')!
    fireEvent.change(input, { target: { value: '999' } })
    fireEvent.blur(input)
    expect(patch).toHaveBeenCalledWith('max', 48, expect.any(Function), 'Max tiles')
  })
})
