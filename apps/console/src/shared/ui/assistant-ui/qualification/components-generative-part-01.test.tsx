import { describe, expect, it } from 'vitest'
import { bindDonorUISpec, firstGenerativeTemplates, parseLiveUISpec, requiredBindings } from '../generative/uispec'
import { donorStructures } from '../generative/donorStructures'
import { planUISpecRender } from '../generative/adapter'
import { availableCapability, dispatchUISpecAction } from '../generative/actions'
import { boundRecord } from './generativeLiveData'

const cases = [
  ['stays', 'book_stay'],
  ['booking', 'book_reservation'],
  ['order-status', 'view_order_event'],
  ['flight-tracker', null],
  ['portfolio', null],
  ['create-event', 'add_to_calendar'],
] as const

function payload(template: string, tree: unknown = { $type: 'Card', title: 'Live record' }) {
  return { template, producer: 'tool:calendar.events', recordId: 'record-42', tree }
}

describe('bounded live UISpec intake', () => {
  it.each(cases)('accepts a live %s structure with its declared action', (template, action) => {
    const tree = { $type: 'Card', children: action
      ? { $type: 'Button', $key: 'action', $action: { type: action }, children: { $type: 'Text', value: 'Continue' } }
      : { $type: 'Text', value: 'Current data' } }
    expect(parseLiveUISpec(payload(template, tree))).toMatchObject({ template, producer: 'tool:calendar.events', recordId: 'record-42' })
  })

  it('keeps the six donor template identities and their action contracts distinct', () => {
    expect(Object.keys(firstGenerativeTemplates)).toEqual(cases.map(([template]) => template))
    expect(firstGenerativeTemplates['order-status']).toEqual(['view_order_event', 'track_order', 'contact_support'])
  })

  it.each([null, 1, [], {}, { template: 'stays', producer: 'x', recordId: 'r' }])
  ('rejects missing or invalid envelopes', (input) => {
    expect(parseLiveUISpec(input)).toBeNull()
  })

  it.each([
    payload('unknown'),
    { ...payload('stays'), producer: '' },
    { ...payload('stays'), recordId: '' },
    { ...payload('stays'), producer: 'x'.repeat(129) },
    { ...payload('stays'), recordId: 'x'.repeat(129) },
    { ...payload('stays'), template: 'x'.repeat(49) },
  ])('requires a known template and bounded source identity', (input) => {
    expect(parseLiveUISpec(input)).toBeNull()
  })

  it('rejects foreign actions, including a valid action on the wrong template', () => {
    expect(parseLiveUISpec(payload('booking', { $type: 'Button', $action: { type: 'send_email' } }))).toBeNull()
    expect(parseLiveUISpec(payload('booking', { $type: 'Button', $action: { type: 'book_stay' } }))).toBeNull()
    expect(parseLiveUISpec(payload('portfolio', { $type: 'Button', $action: { type: 'book_stay' } }))).toBeNull()
  })

  it('rejects action arguments carried by the model', () => {
    expect(parseLiveUISpec(payload('stays', { $type: 'Button', $action: { type: 'book_stay', stayId: 'injected' } }))).toBeNull()
    expect(parseLiveUISpec(payload('stays', { $type: 'Button', $action: null }))).toBeNull()
    expect(parseLiveUISpec(payload('stays', { $type: 'Button', $action: { type: '' } }))).toBeNull()
  })

  it('requires known component types and bounded unique keys', () => {
    expect(parseLiveUISpec(payload('stays', { $type: 'Script' }))).toBeNull()
    expect(parseLiveUISpec(payload('stays', { $type: '' }))).toBeNull()
    expect(parseLiveUISpec(payload('stays', { $type: 'Card', $key: '' }))).toBeNull()
    expect(parseLiveUISpec(payload('stays', { $type: 'Card', $key: 'x'.repeat(129) }))).toBeNull()
    expect(parseLiveUISpec(payload('stays', { $type: 'Card', children: [{ $type: 'Text', $key: 'same' }, { $type: 'Text', $key: 'same' }] }))).toBeNull()
  })

  it('rejects event props and unknown control fields', () => {
    expect(parseLiveUISpec(payload('stays', { $type: 'Card', onClick: 'alert(1)' }))).toBeNull()
    expect(parseLiveUISpec(payload('stays', { $type: 'Card', $component: 'iframe' }))).toBeNull()
    expect(parseLiveUISpec(payload('stays', { $type: 'Card', $key: 9 }))).toBeNull()
  })

  it('bounds strings, arrays, object depth and non-finite numbers', () => {
    expect(parseLiveUISpec(payload('stays', { $type: 'Text', value: 'x'.repeat(4097) }))).toBeNull()
    expect(parseLiveUISpec(payload('stays', { $type: 'Table', rows: Array(129).fill(1) }))).toBeNull()
    expect(parseLiveUISpec(payload('stays', { $type: 'Chart', value: Infinity }))).toBeNull()
    expect(parseLiveUISpec(payload('stays', { $type: 'Text', value: { a: { b: { c: { d: { e: 1 } } } } } }))).toBeNull()
    expect(parseLiveUISpec(payload('stays', { $type: 'Text', value: Object.fromEntries(Array.from({ length: 33 }, (_, i) => [i, i])) }))).toBeNull()
  })

  it('bounds tree depth and total nodes', () => {
    let deep: unknown = { $type: 'Text', value: 'leaf' }
    for (let i = 0; i < 17; i++) deep = { $type: 'Box', children: deep }
    expect(parseLiveUISpec(payload('stays', deep))).toBeNull()
    expect(parseLiveUISpec(payload('stays', { $type: 'ListView', children: Array.from({ length: 256 }, (_, i) => ({ $type: 'Text', $key: `${i}` })) }))).toBeNull()
  })

  it('allows realistic scalar, option and row data', () => {
    const tree = { $type: 'Card', children: [
      { $type: 'Select', $key: 'guest-count', name: 'guests', options: [{ label: 'Two', value: '2' }] },
      { $type: 'Table', $key: 'orders', rows: [{ id: 'record-42', total: 19.5, paid: true, note: null }] },
    ] }
    expect(parseLiveUISpec(payload('booking', tree))?.tree).toEqual(tree)
  })
})

describe('part 01 donor structures and live bindings', () => {
  it.each(cases)('materializes %s only when every sample value has a typed live replacement', (template) => {
    const record = boundRecord(template)
    const spec = bindDonorUISpec(record)
    expect(spec?.tree.$type).toBe(donorStructures.find((item) => item.slug === template)?.tree.$type)
    expect(JSON.stringify(spec?.tree)).not.toContain('riverside-loft')
    expect(requiredBindings(template).length).toBeGreaterThan(0)
    const missing = { ...record, bindings: { ...record.bindings } }
    delete missing.bindings[requiredBindings(template)[0]]
    expect(bindDonorUISpec(missing)).toBeNull()
  })

  it('rejects extra, wrong-type and unsafe URL bindings', () => {
    const record = boundRecord('stays')
    expect(bindDonorUISpec({ ...record, bindings: { ...record.bindings, injected: 'sample' } })).toBeNull()
    expect(bindDonorUISpec({ ...record, bindings: { ...record.bindings, title: 17 } })).toBeNull()
    expect(bindDonorUISpec({ ...record, bindings: { ...record.bindings, 'children.0.src': 'javascript:alert(1)' } })).toBeNull()
  })

  it('shows an honest unavailable state for a booking without a provider', () => {
    const spec = bindDonorUISpec(boundRecord('stays'))!
    const plan = planUISpecRender(spec)
    expect(plan.unavailable).toEqual(['book_stay'])
    expect(JSON.stringify(plan.tree)).toContain('No connected provider')
    expect(JSON.stringify(plan.tree)).not.toContain('"$action"')
    expect(spec.tree.children).toBeTruthy()
  })

  it('refuses a mutation wired only to a read-only callback', async () => {
    const spec = bindDonorUISpec(boundRecord('stays'))!
    const capability = { book_stay: { kind: 'open-record' as const, producer: spec.producer, recordId: spec.recordId, open: () => true } }
    expect(availableCapability(spec, 'book_stay', capability)).toBeNull()
    expect((await dispatchUISpecAction(spec, { type: 'book_stay' }, capability)).outcome).toBe('unavailable')
  })

  it('passes only the live record scope through an explicitly supplied preview and confirm handler', async () => {
    const spec = bindDonorUISpec(boundRecord('booking'))!
    const seen: unknown[] = []
    const capability = { book_reservation: {
      kind: 'preview-confirm' as const, producer: spec.producer, recordId: spec.recordId,
      previewAndConfirm: async (scope: { producer: string; recordId: string; fields: Record<string, unknown> }) => {
        seen.push(scope)
        return { status: 'cancelled' as const, message: 'Reservation cancelled during preview.' }
      },
    } }
    expect(planUISpecRender(spec, capability).unavailable).toEqual(['cancel'])
    expect(await dispatchUISpecAction(spec, { type: 'book_reservation', $input: { guestName: 'Ada' } }, capability))
      .toEqual({ ok: false, outcome: 'cancelled', message: 'Reservation cancelled during preview.' })
    expect(seen).toEqual([{ producer: spec.producer, recordId: spec.recordId, fields: { guestName: 'Ada' } }])
  })
})
