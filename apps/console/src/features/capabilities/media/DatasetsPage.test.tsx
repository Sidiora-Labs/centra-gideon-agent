import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import DatasetsPage, { DatasetEntries } from './DatasetsPage'
import { JobCard } from './JobsPage'

describe('captioned datasets and checkpoint training surface', () => {
  it('starts with no invented dataset and an unavailable training action', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<DatasetsPage />), 'text/html')
    expect(doc.querySelector('h1')?.textContent).toBe('LoRA datasets and training')
    expect(doc.body.textContent).toContain('pinned image references')
    expect(doc.body.textContent).toContain('preserves its prior revision')
    expect(doc.body.textContent).toContain('separately installed and admitted')
    expect(doc.body.textContent).toContain('Loading trainer readiness')
    expect(doc.body.textContent).toContain('does not verify GPU readiness')
    expect(doc.querySelector('select')?.value).toBe('')
    expect(doc.querySelectorAll('option')).toHaveLength(1)
    const buttons = [...doc.querySelectorAll('button')]
    expect(buttons.find(button => button.textContent === 'Queue training')?.disabled).toBe(true)
    expect(buttons.find(button => button.textContent === 'Save dataset revision')?.disabled).toBe(true)
    expect(buttons.find(button => button.textContent === 'Add image')?.disabled).toBe(true)
    expect(buttons.find(button => button.textContent === 'Discard changes')).toBeTruthy()
    expect(doc.querySelectorAll('li')).toHaveLength(0)
    expect(doc.querySelector('[role="alert"]')).toBeNull()
  })
  it('renders authored captions with exact pinned reference versions', () => {
    const entries = [{ artifact_id: 'source', version: 2, caption: '红色图像\nصورة حمراء' }, { artifact_id: 'other', version: 1, caption: '<script>not executed</script>' }]
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<DatasetEntries entries={entries} change={() => {}} />), 'text/html')
    expect(doc.querySelectorAll('li')).toHaveLength(2)
    expect(doc.querySelectorAll('p')[0].textContent).toBe('source · version 2')
    expect(doc.querySelectorAll('p')[1].textContent).toBe('other · version 1')
    const captions = [...doc.querySelectorAll('textarea')]
    expect(captions[0].value).toBe(entries[0].caption)
    expect(captions[1].value).toBe(entries[1].caption)
    expect(captions[0].maxLength).toBe(2000)
    expect(doc.querySelector('script')).toBeNull()
    expect(doc.querySelectorAll('button')).toHaveLength(2)
    expect(doc.querySelector('button')?.textContent).toBe('Remove image')
    expect(doc.querySelector('img')).toBeNull()
  })
  it('uses documented bounded training parameter inputs', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<DatasetsPage />), 'text/html')
    const controls = [...doc.querySelectorAll('label')]
    const field = (name: string) => controls.find(label => label.textContent?.startsWith(name))?.querySelector('input')!
    expect(field('Steps').min).toBe('1')
    expect(field('Steps').max).toBe('50000')
    expect(field('Steps').value).toBe('100')
    expect(field('Rank').max).toBe('128')
    expect(field('Rank').value).toBe('4')
    expect(field('Learning rate').max).toBe('0.01')
    expect(field('Learning rate').step).toBe('any')
    expect(field('Seed').value).toBe('0')
    expect(field('Seed').max).toBe('4294967295')
    expect(doc.querySelector('a[href*="export"]')).toBeNull()
  })
  it('renders training output as an adapter and exposes checkpoint inventory', () => {
    const job = { id: 'job', operation: 'lora_train', sketch_id: '', revision: 0, state_revision: 3, attempt: 1, status: 'succeeded', error: null, result: { adapter_id: 'trained-job.safetensors' }, events: [] }
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<JobCard job={job} busy={false} act={() => {}} />), 'text/html')
    expect(doc.querySelector('h2')?.textContent).toBe('LoRA training')
    expect(doc.body.textContent).toContain('Trained adapter: trained-job.safetensors')
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('/api/capabilities/media/jobs/job/checkpoints')
    expect(doc.querySelector('a')?.textContent).toBe('Retained checkpoint inventory')
    expect(doc.body.textContent).not.toContain('Open rendered PNG')
    expect(doc.querySelector('button')).toBeNull()
    expect(doc.querySelectorAll('a')).toHaveLength(1)
  })
})
