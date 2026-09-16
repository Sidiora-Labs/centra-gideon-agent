import { describe, expect, it } from 'vitest'
import { useState } from 'react'
import { render, within, fireEvent } from '@testing-library/react'
import { SchemaField, buildArgs, seedArgs, type JsonSchema } from './schema'


function BooleanForm({ parameters }: { parameters: JsonSchema }) {
  const [args, setArgs] = useState<Record<string, unknown>>(() => seedArgs(parameters))
  const required = new Set(parameters.required ?? [])
  return (
    <div>
      <output data-testid="wire">{JSON.stringify(buildArgs(parameters, args).args)}</output>
      {Object.entries(parameters.properties ?? {}).map(([name, s]) => (
        <SchemaField key={name} name={name} schema={s} required={required.has(name)}
          value={args[name]} onChange={(v) => setArgs((a) => ({ ...a, [name]: v }))} />
      ))}
    </div>
  )
}

const optional: JsonSchema = {
  type: 'object',
  properties: { dry_run: { type: 'boolean', 'x-meta': { label: 'Dry Run' } } },
}

function renderForm(parameters: JsonSchema) {
  const { container } = render(<BooleanForm parameters={parameters} />)
  const q = within(container as HTMLElement)
  return {
    wire: () => JSON.parse(q.getByTestId('wire').textContent || '{}') as Record<string, unknown>,
    toggle: (label: string) => q.getByRole('switch', { name: label }),
  }
}

describe('seedArgs leaves an unset optional boolean unset', () => {
  it('omits a default-less optional boolean from the invoke', () => {
    expect(seedArgs(optional)).toEqual({ dry_run: '' })
    expect(renderForm(optional).wire()).toEqual({})
  })

  it('honours a default the schema DOES declare', () => {
    const declared: JsonSchema = {
      type: 'object',
      properties: { require_approval: { type: 'boolean', default: true } },
    }
    expect(seedArgs(declared)).toEqual({ require_approval: true })
    expect(renderForm(declared).wire()).toEqual({ require_approval: true })
  })

  it('keeps a REQUIRED boolean at false — buildArgs never drops a required key', () => {
    const req: JsonSchema = { type: 'object', required: ['strict'], properties: { strict: { type: 'boolean' } } }
    expect(seedArgs(req)).toEqual({ strict: false })
    expect(renderForm(req).wire()).toEqual({ strict: false })
  })
})

describe('touching the switch is still an answer', () => {
  it('sends true once the user turns it on', () => {
    const form = renderForm(optional)
    fireEvent.click(form.toggle('Dry Run'))
    expect(form.wire()).toEqual({ dry_run: true })
  })

  it('sends an EXPLICIT false after on → off, rather than dropping it', () => {
    const form = renderForm(optional)
    fireEvent.click(form.toggle('Dry Run'))
    fireEvent.click(form.toggle('Dry Run'))
    expect(form.wire()).toEqual({ dry_run: false })
  })
})
