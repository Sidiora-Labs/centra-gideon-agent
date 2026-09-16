import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Segmented } from './Segmented'
import { Field } from './forms'


const SRC = join(process.cwd(), "src")

const EXPLICIT: Array<[string, string]> = [
  [join('features', 'artifacts', 'ArtifactsSection.tsx'), 'Artifact kind'],
  [join('features', 'files', 'FilesSection.tsx'), 'File root'],
  [join('features', 'inbox', 'InboxDetail.tsx'), 'Reclassify'],
  [join('features', 'learning', 'LearningPage.tsx'), 'Proposal kind'],
]

describe('Segmented claims its Field label', () => {
  it('inside a Field, the tablist is named by the Field', () => {
    const { container } = render(
      <Field label="Priority">
        <Segmented value="a" onChange={() => {}} options={[{ key: 'a', label: 'High' }, { key: 'b', label: 'Low' }]} />
      </Field>,
    )
    const list = container.querySelector('[role="tablist"]')!
    const id = list.getAttribute('aria-labelledby')
    expect(id, 'the group must claim the published label id').toBeTruthy()
    expect(document.getElementById(id!)!.textContent).toBe('Priority')
    expect(list.getAttribute('aria-label'), 'and must not also carry a redundant aria-label').toBeNull()
  })

  it('an explicit ariaLabel WINS over the Field label', () => {
    const { container } = render(
      <Field label="Priority">
        <Segmented ariaLabel="Sort artifacts" value="a" onChange={() => {}} options={[{ key: 'a', label: 'High' }]} />
      </Field>,
    )
    const list = container.querySelector('[role="tablist"]')!
    expect(list.getAttribute('aria-label')).toBe('Sort artifacts')
    expect(list.getAttribute('aria-labelledby')).toBeNull()
  })

  it('outside a Field with no ariaLabel, nothing is invented', () => {
    const { container } = render(
      <Segmented value="a" onChange={() => {}} options={[{ key: 'a', label: 'High' }]} />,
    )
    const list = container.querySelector('[role="tablist"]')!
    expect(list.getAttribute('aria-labelledby')).toBeNull()
    expect(list.getAttribute('aria-label')).toBeNull()
  })
})

describe('the bare tablists carry an explicit name', () => {
  it.each(EXPLICIT)('%s names one of its groups "%s"', (rel, name) => {
    const src = readFileSync(join(SRC, rel), 'utf8')
    expect(src, `${rel} must name that group`).toContain(`ariaLabel="${name}"`)
  })

  it('the primitive still reads the Field context (not vacuously green)', () => {
    const src = readFileSync(join(SRC, "shared/ui", 'Segmented.tsx'), 'utf8')
    expect(src).toMatch(/import \{ useFieldLabelId \} from '\.\/forms'/)
    expect(src).toContain("const naming = ariaLabel ? { 'aria-label': ariaLabel } : { 'aria-labelledby': fieldLabelId }")
  })
})
