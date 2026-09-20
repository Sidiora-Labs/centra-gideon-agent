import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { SchemaField } from './ProviderConfigForm'
import type { ProviderSchemaProp } from '../../shared/data/api'

afterEach(() => cleanup())

function field(prop: ProviderSchemaProp) {
  const { container } = render(
    <SchemaField fieldKey="api_key" prop={prop} value={undefined} onChange={() => {}} />,
  )
  return container.querySelector('input') as HTMLInputElement
}

describe('provider config form mirrors declared constraints', () => {
  it('string constraints land as minLength/maxLength/pattern on a text input', () => {
    const el = field({ type: 'string', minLength: 8, maxLength: 40, pattern: '^sk-' })
    expect(el.type).toBe('text')
    expect(el.minLength).toBe(8)
    expect(el.maxLength).toBe(40)
    expect(el.pattern).toBe('^sk-')
  })

  it('numeric bounds still land as min/max', () => {
    const el = field({ type: 'integer', minimum: 1, maximum: 600 })
    expect(el.type).toBe('number')
    expect(el.min).toBe('1')
    expect(el.max).toBe('600')
  })

  it('a sensitive field takes the length bounds but not the pattern', () => {
    const el = field({
      type: 'string',
      minLength: 8,
      pattern: '^sk-',
      'x-meta': { sensitive: true },
    })
    expect(el.type).toBe('password')
    expect(el.minLength).toBe(8)
    expect(el.getAttribute('pattern')).toBeNull()
  })

  it('an unconstrained field renders no stray attributes', () => {
    const el = field({ type: 'string' })
    expect(el.getAttribute('pattern')).toBeNull()
    expect(el.getAttribute('minlength')).toBeNull()
    expect(el.getAttribute('maxlength')).toBeNull()
  })

  it.each([
    { type: 'string' },
    { type: 'integer' },
    { type: 'string', enum: ['one', 'two'] },
    { type: 'object' },
  ] as ProviderSchemaProp[])('binds each primitive control to its visible label', (prop) => {
    const { container } = render(
      <SchemaField fieldKey="endpoint" prop={{ ...prop, 'x-meta': { label: 'Endpoint' } }} value={undefined} onChange={() => {}} />,
    )
    const label = container.querySelector('label')!
    const control = container.querySelector('input, select, textarea')!
    expect(label.htmlFor).toBe(control.id)
    expect(control.className.split(/\s+/)).not.toContain('px-3')
  })

  it('the secret eye toggles the primitive input type', () => {
    const { container, getByRole } = render(
      <SchemaField fieldKey="api_key" prop={{ type: 'string', 'x-meta': { sensitive: true } }} value="secret" onChange={() => {}} />,
    )
    const input = container.querySelector('input')!
    expect(input.type).toBe('password')
    fireEvent.click(getByRole('button', { name: 'Show' }))
    expect(input.type).toBe('text')
    fireEvent.click(getByRole('button', { name: 'Hide' }))
    expect(input.type).toBe('password')
  })
})
