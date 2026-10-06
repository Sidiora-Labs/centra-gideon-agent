import { fireEvent, render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { draftOf, draftProblem, rateOf, rateText, sourceText, RateForm } from './ModelPricesSection'
import type { ModelRateFields, ModelRateProvenance } from '../../shared/data/api'

const rate = (extra: Partial<ModelRateFields> = {}): ModelRateFields => ({ unit: 'token', in_per_mtok: 1, out_per_mtok: 2, cache_read_per_mtok: null, cache_write_per_mtok: null, per_unit: null, tiers: [], default_size: '', default_quality: '', ...extra })

describe('unit pricing controls', () => {
  it('preserves explicit zero and optional cache fields', () => {
    const draft = draftOf('Cloud:text', rate({ in_per_mtok: 0, out_per_mtok: 0 }))
    expect(draftProblem(draft)).toBe('')
    expect(rateOf(draft)).toMatchObject({ in_per_mtok: 0, out_per_mtok: 0, cache_read_per_mtok: null })
    expect(draftProblem({ ...draft, input: '-1' })).toContain('zero or more')
    expect(draftProblem({ ...draft, input: 'Infinity' })).toContain('zero or more')
  })
  it('uses the proper scalar field for every billed unit', () => {
    for (const [unit, field] of [['image', 'per_image'], ['second', 'per_second'], ['minute', 'per_minute'], ['character', 'per_mchar']] as const) {
      const draft = draftOf('Cloud:media', rate({ unit, per_unit: .05 }))
      expect(draftProblem(draft)).toBe('')
      expect(rateOf(draft)).toHaveProperty(field, .05)
      expect(rateOf(draft)).not.toHaveProperty('in_per_mtok')
    }
  })
  it('keeps image tiers, defaults, size validation and list provenance', () => {
    const image = rate({ unit: 'image', tiers: [{ size: '1024x1024', quality: 'standard', per_image: .04 }], default_size: '1024x1024', default_quality: 'standard' })
    const draft = draftOf('Cloud:image', image)
    expect(rateOf(draft)).toMatchObject({ tiers: image.tiers, default_size: image.default_size })
    expect(rateText(image)).toContain('per image')
    expect(draftProblem({ ...draft, defaultSize: '0x1024' })).toContain('positive')
    const provenance: ModelRateProvenance = { ...image, source: 'builtin', vendor: 'Maker', recorded: '2026-10-01', priced_as: 'image' }
    expect(sourceText(provenance)).toContain('Maker list price')
    expect(sourceText(provenance)).toContain('recorded 2026-10-01')
  })
  it('renders actual native controls and switches from tokens to audio pricing', () => {
    render(<RateForm initial={draftOf('Cloud:text', rate())} locked choices={['Cloud:text']} onSaved={() => {}} onCancel={() => {}} />)
    expect(screen.getByLabelText('Input, $ per 1M tokens')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Billed unit'), { target: { value: 'minute' } })
    expect(screen.getByLabelText('Price, $ per minute of audio')).toBeTruthy()
    expect(screen.queryByLabelText('Input, $ per 1M tokens')).toBeNull()
  })
})
