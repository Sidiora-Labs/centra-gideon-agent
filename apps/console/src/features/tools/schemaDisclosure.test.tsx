import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { SchemaFieldDisclosure, rankSchemaFields, type SchemaFieldEntry } from './schema'

afterEach(() => cleanup())

const FIELDS: SchemaFieldEntry[] = [
  ['name', { type: 'string' }],
  ['required_advanced', { type: 'string', 'x-meta': { tags: ['advanced'] } }],
  ['retry_limit', { type: 'number', 'x-meta': { tags: ['advanced'] } }],
  ['timeout', { type: 'number', 'x-meta': { tags: ['advanced'] } }],
]

describe('SchemaFieldDisclosure', () => {
  it('keeps required advanced fields ranked with standard settings', () => {
    const ranked = rankSchemaFields(FIELDS, ['required_advanced'])
    expect(ranked.standard.map(([name]) => name)).toEqual(['name', 'required_advanced'])
    expect(ranked.advanced.map(([name]) => name)).toEqual(['retry_limit', 'timeout'])
  })

  it('announces hidden and configured advanced counts and reveals the fields', () => {
    render(
      <SchemaFieldDisclosure fields={FIELDS} required={['required_advanced']} values={{ retry_limit: 3 }}
        renderField={([name]) => <div key={name}>{name}</div>} />,
    )

    expect(screen.getByText('name')).toBeTruthy()
    expect(screen.getByText('required_advanced')).toBeTruthy()
    expect(screen.queryByText('retry_limit')).toBeNull()
    const disclosure = screen.getByRole('button', { name: 'Advanced settings' })
    expect(disclosure).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('2 hidden advanced settings, 1 configured value.')).toBeTruthy()

    fireEvent.click(disclosure)
    expect(disclosure).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('retry_limit')).toBeTruthy()
    expect(screen.getByText('0 hidden advanced settings, 1 configured value.')).toBeTruthy()
  })

  it('keeps all-advanced schemas flat', () => {
    const fields: SchemaFieldEntry[] = [['storage_dir', { type: 'string', 'x-meta': { tags: ['advanced'] } }]]
    render(<SchemaFieldDisclosure fields={fields} renderField={([name]) => <div key={name}>{name}</div>} />)
    expect(screen.getByText('storage_dir')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Advanced settings' })).toBeNull()
  })
})
