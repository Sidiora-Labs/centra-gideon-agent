import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { SchemaField } from './ModelBackends'
import { SchemaField as SharedSchemaField } from '../tools/schema'

afterEach(cleanup)

describe('model binding numeric schema', () => {
  it.each([
    ['context_window', 'integer', 1, 2147483647, '1'],
    ['timeout_secs', 'number', 1, 3600, 'any'],
  ] as const)('renders bounds and numeric type for %s', (name, type, minimum, maximum, step) => {
    const field = { type, minimum, maximum }
    const { unmount } = render(<SchemaField name={name} field={field} value="4096" onChange={() => {}} />)
    let input = screen.getByRole('spinbutton', { name }) as HTMLInputElement
    expect(input.min).toBe(String(minimum))
    expect(input.max).toBe(String(maximum))
    expect(input.step).toBe(step)
    input.value = '0'
    expect(input.validity.rangeUnderflow).toBe(true)
    input.value = String(maximum + 1)
    expect(input.validity.rangeOverflow).toBe(true)
    unmount()
    render(<SharedSchemaField name={name} schema={field} value={4} required={false} onChange={() => {}} />)
    input = screen.getByRole('spinbutton', { name }) as HTMLInputElement
    expect(input.min).toBe(String(minimum))
    expect(input.max).toBe(String(maximum))
    expect(input.step).toBe(step)
  })
})
