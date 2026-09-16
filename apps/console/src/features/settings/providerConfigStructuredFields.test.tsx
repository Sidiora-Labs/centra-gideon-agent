import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { SchemaField } from './ProviderConfigForm'
import type { ProviderSchemaProp } from '../../shared/data/api'

afterEach(() => cleanup())

function renderField(prop: ProviderSchemaProp, value: unknown, extra: { secretAlreadySet?: boolean } = {}) {
  const onChange = vi.fn()
  const { container } = render(
    <SchemaField fieldKey="allowed_users" prop={prop} value={value} onChange={onChange} {...extra} />,
  )
  return { container, onChange }
}

const ARRAY_PROP: ProviderSchemaProp = {
  type: 'array',
  'x-meta': { label: 'Allowed Users', help: 'Each entry: {slack_id, name}.' },
}

describe('a structured provider setting is editable as JSON', () => {
  it('renders an array of objects as JSON, not "[object Object]"', () => {
    const { container } = renderField(ARRAY_PROP, [{ slack_id: 'U_ALICE', name: 'Alice' }])
    const ta = container.querySelector('textarea') as HTMLTextAreaElement
    expect(ta).toBeTruthy()
    expect(ta.value).not.toContain('[object Object]')
    expect(JSON.parse(ta.value)).toEqual([{ slack_id: 'U_ALICE', name: 'Alice' }])
    expect(container.querySelector('input')).toBeNull()
  })

  it('an unset array starts as an empty array, so the operator can type into it', () => {
    const { container } = renderField(ARRAY_PROP, undefined)
    expect((container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('[]')
  })

  it('a valid edit commits the parsed value', () => {
    const { container, onChange } = renderField(ARRAY_PROP, [])
    fireEvent.change(container.querySelector('textarea')!, {
      target: { value: '[{"slack_id":"U_BOB"}]' },
    })
    expect(onChange).toHaveBeenCalledWith([{ slack_id: 'U_BOB' }])
  })

  it('invalid JSON explains itself and commits NOTHING', () => {
    const { container, onChange } = renderField(ARRAY_PROP, [{ slack_id: 'U_ALICE' }])
    fireEvent.change(container.querySelector('textarea')!, { target: { value: '[{"slack_id":' } })
    expect(onChange).not.toHaveBeenCalled()
    expect(container.textContent).toContain('⚠')
    expect(container.textContent).toContain('Each entry')
  })

  it('an object field gets the same editor, seeded as {}', () => {
    const { container } = renderField({ type: 'object', 'x-meta': { label: 'Reactions' } }, undefined)
    expect((container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('{}')
  })
})

describe('a stored secret is legible as stored', () => {
  const SECRET: ProviderSchemaProp = { type: 'string', 'x-meta': { label: 'Bot Token', sensitive: true } }

  it('a set secret renders blank with a "saved" placeholder, never the value', () => {
    const { container } = renderField(SECRET, '', { secretAlreadySet: true })
    const el = container.querySelector('input') as HTMLInputElement
    expect(el.type).toBe('password')
    expect(el.value).toBe('')
    expect(el.placeholder).toBe('saved — leave blank to keep')
  })

  it('an UNSET secret keeps the neutral placeholder — "not configured" is not a secret', () => {
    const { container } = renderField(SECRET, '')
    expect((container.querySelector('input') as HTMLInputElement).placeholder).not.toMatch(/saved/)
  })
})
