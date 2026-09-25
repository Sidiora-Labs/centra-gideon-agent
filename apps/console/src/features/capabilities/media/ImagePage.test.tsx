import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import ImagePage, { ConditioningFields, type ImageCapabilities } from './ImagePage'
import { JobCard } from './JobsPage'

const model = { name: 'image', sizes: ['512x512'], supports_edit: false, supports_mask: false, controls: {} }
const caps: ImageCapabilities = { selection: 'provider:image', available: true, models: [model] }
function render(capabilities: ImageCapabilities, values = {}) {
  return new DOMParser().parseFromString(renderToStaticMarkup(<ConditioningFields capabilities={capabilities} values={values} change={() => {}} />), 'text/html')
}
describe('image generation controls', () => {
  it('shows the pinned-source and unavailable-provider boundary while loading', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<ImagePage />), 'text/html')
    expect(doc.querySelector('h1')?.textContent).toBe('Image generation')
    expect(doc.body.textContent).toContain('pin the selected model')
    expect(doc.body.textContent).toContain('separate artifact')
    expect(doc.body.textContent).toContain('Unsupported controls fail before inference')
    expect(doc.body.textContent).toContain('Loading image capabilities')
    expect(doc.querySelector('button')).toBeNull()
    expect(doc.querySelector('textarea')).toBeNull()
    expect(doc.querySelectorAll('a')).toHaveLength(2)
  })
  it('does not invent an absent model control catalog', () => {
    const doc = render({ selection: '', available: false, models: [] })
    expect(doc.body.textContent).toContain('No selected model capability catalog')
    expect(doc.querySelector('input')).toBeNull()
    expect(doc.querySelector('select')).toBeNull()
    expect(doc.querySelector('fieldset')).toBeNull()
  })
  it('does not offer unsupported knobs or conditioning', () => {
    const doc = render(caps)
    expect(doc.querySelectorAll('option')).toHaveLength(2)
    expect(doc.querySelectorAll('option')[1].textContent).toBe('512x512')
    expect(doc.body.textContent).toContain('no seed, steps, guidance or strength controls')
    expect(doc.body.textContent).toContain('Image conditioning is not advertised')
    expect(doc.querySelectorAll('input')).toHaveLength(0)
    expect(doc.body.textContent).not.toContain('Mask image artifact')
  })
  it('represents advertised numeric bounds and integer steps', () => {
    const doc = render({ ...caps, models: [{ ...model, controls: { seed: { minimum: 0, maximum: 100, integer: true }, strength: { minimum: 0, maximum: 1, integer: false } } }] }, { seed: '42', strength: '.5' })
    const inputs = [...doc.querySelectorAll('input')]
    expect(inputs).toHaveLength(2)
    expect(inputs[0].min).toBe('0')
    expect(inputs[0].max).toBe('100')
    expect(inputs[0].step).toBe('1')
    expect(inputs[0].value).toBe('42')
    expect(inputs[1].max).toBe('1')
    expect(inputs[1].step).toBe('any')
    expect(inputs[1].value).toBe('.5')
  })
  it('renders separate source and mask version references', () => {
    const doc = render({ ...caps, models: [{ ...model, supports_edit: true, supports_mask: true }] }, { source_artifact_id: 'source', source_version: '2', mask_artifact_id: 'mask', mask_version: '3' })
    const inputs = [...doc.querySelectorAll('input')]
    expect(inputs.map(input => input.value)).toEqual(['source', '2', 'mask', '3'])
    expect(inputs[1].min).toBe('1')
    expect(inputs[3].min).toBe('1')
    expect(doc.body.textContent).toContain('Mask dimensions must match')
    expect(doc.body.textContent).toContain('mask alpha is preserved')
    expect(doc.body.textContent).not.toContain('Image conditioning is not advertised')
  })
  it('labels image jobs by prompt and keeps real failure state visible', () => {
    const job = { id: 'image-job', operation: 'image_generate', input: { prompt: '<b>Draw image</b>' }, sketch_id: '', revision: 0, state_revision: 3, attempt: 1, status: 'failed', error: 'Provider unavailable', result: null, events: [] }
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<JobCard job={job} busy={false} act={() => {}} />), 'text/html')
    expect(doc.querySelector('h2')?.textContent).toBe('Image generation: <b>Draw image</b>')
    expect(doc.querySelector('b')).toBeNull()
    expect(doc.querySelector('[role="alert"]')?.textContent).toBe('Provider unavailable')
    expect(doc.querySelector('button')?.textContent).toBe('Retry')
    expect(doc.querySelector('a')).toBeNull()
  })
})
