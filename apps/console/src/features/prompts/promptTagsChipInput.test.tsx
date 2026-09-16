import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import type { ReactNode } from 'react'


vi.mock('../../shared/data/api', () => ({
  api: new Proxy({}, { get: () => () => Promise.resolve(null) }),
}))

import { PromptEditFields } from './PromptEditFields'
import { emptyDraft, type PromptDraft } from './PromptForm'

function Section({ label, children }: { label: string; children?: ReactNode }) {
  return (
    <div>
      <div>{label}</div>
      {children}
    </div>
  )
}

function Harness({ onDraft }: { onDraft?: (d: PromptDraft) => void }) {
  const [draft, setDraft] = useState<PromptDraft>(emptyDraft())
  return (
    <PromptEditFields draft={draft} Section={Section}
      onChange={(next) => { setDraft(next); onDraft?.(next) }} />
  )
}

describe('prompt Tags in edit mode (ChipInput, #681)', () => {
  it('typing "red,green" yields TWO tags, not the single tag "redgreen"', () => {
    const seen: PromptDraft[] = []
    const { getByLabelText } = render(<Harness onDraft={(d) => seen.push(d)} />)
    const field = getByLabelText('Prompt tags') as HTMLInputElement

    fireEvent.change(field, { target: { value: 'red' } })
    fireEvent.keyDown(field, { key: ',' })
    expect(seen.at(-1)?.tags).toEqual(['red'])

    fireEvent.change(field, { target: { value: 'green' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(seen.at(-1)?.tags).toEqual(['red', 'green'])
  })

  it('a trailing comma cannot delete typed state (the original symptom)', () => {
    const seen: PromptDraft[] = []
    const { getByLabelText } = render(<Harness onDraft={(d) => seen.push(d)} />)
    const field = getByLabelText('Prompt tags') as HTMLInputElement
    fireEvent.change(field, { target: { value: 'blue,' } })
    fireEvent.blur(field)
    expect(seen.at(-1)?.tags).toEqual(['blue'])
    fireEvent.change(field, { target: { value: 'x' } })
    expect(seen.at(-1)?.tags).toEqual(['blue'])
  })
})
