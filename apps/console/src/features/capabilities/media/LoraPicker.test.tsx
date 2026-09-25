import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import LoraPicker, { type LoraInventory } from './LoraPicker'
const item = { id: 'adapter.safetensors', sha256: 'a'.repeat(64), bytes: 512, base_model: 'base', compatibility: 'metadata_match', trigger_words: 'red bird', effect_verified: false }
const inventory: LoraInventory = { items: [item], invalid: [], truncated: false, supports_lora: true, selected_base_model: 'base' }
const render = (value: LoraInventory, selected: Record<string, string> = {}) => new DOMParser().parseFromString(renderToStaticMarkup(<LoraPicker inventory={value} selected={selected} change={() => {}} />), 'text/html')
describe('installed adapter picker', () => {
  it('never equates metadata matching with verified image effect', () => {
    const doc = render(inventory)
    expect(doc.querySelector('legend')?.textContent).toBe('Installed LoRA adapters')
    expect(doc.body.textContent).toContain('metadata only')
    expect(doc.body.textContent).toContain('image effect have not been verified')
    expect(doc.body.textContent).toContain('metadata_match')
    expect(doc.body.textContent).toContain(item.sha256)
    expect(doc.body.textContent).toContain('Trigger words: red bird')
    expect(doc.querySelector<HTMLInputElement>('input')?.disabled).toBe(false)
    expect(doc.querySelector<HTMLInputElement>('input')?.checked).toBe(false)
    expect(doc.querySelectorAll('input')).toHaveLength(1)
  })
  it('disables application when the provider does not advertise support', () => {
    const doc = render({ ...inventory, supports_lora: false })
    expect(doc.querySelector('[role="status"]')?.textContent).toContain('does not advertise LoRA support')
    expect(doc.querySelector<HTMLInputElement>('input')?.disabled).toBe(true)
    expect(doc.body.textContent).toContain(item.id)
    expect(doc.querySelectorAll('article')).toHaveLength(1)
  })
  it('does not allow unknown or mismatched adapters to be selected', () => {
    for (const compatibility of ['unknown', 'metadata_mismatch']) {
      const doc = render({ ...inventory, items: [{ ...item, compatibility }] })
      expect(doc.querySelector<HTMLInputElement>('input')?.disabled).toBe(true)
      expect(doc.body.textContent).toContain(compatibility)
      expect(doc.querySelector('input[type="number"]')).toBeNull()
    }
  })
  it('exposes bounded signed scales for selected adapters', () => {
    const doc = render(inventory, { [item.id]: '-0.5' })
    expect(doc.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked).toBe(true)
    const scale = doc.querySelector<HTMLInputElement>('input[type="number"]')!
    expect(scale.min).toBe('-2')
    expect(scale.max).toBe('2')
    expect(scale.step).toBe('0.1')
    expect(scale.value).toBe('-0.5')
  })
  it('shows invalid containers and truncated discovery without inventing adapters', () => {
    const doc = render({ ...inventory, items: [], invalid: [{ id: '<b>bad.safetensors</b>', error: 'Invalid tensor offsets' }], truncated: true })
    expect(doc.body.textContent).toContain('No installed adapters discovered')
    expect(doc.body.textContent).toContain('some files were not inspected')
    expect(doc.querySelector('[role="alert"]')?.textContent).toContain('Invalid tensor offsets')
    expect(doc.querySelector('b')).toBeNull()
    expect(doc.querySelector('input')).toBeNull()
    expect(doc.querySelector('article')).toBeNull()
  })
})
