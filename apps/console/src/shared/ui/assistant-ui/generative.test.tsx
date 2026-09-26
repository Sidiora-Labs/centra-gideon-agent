import { describe, expect, it } from 'vitest'
import { donorStructures, donorGallerySha256 } from './generative/donorStructures'
import trace from './generative/trace.json'
import { bindDonorUISpec, generativeActions, parseLiveUISpec, requiredBindings, type GenerativeTemplate } from './generative/uispec'
import { planUISpecRender } from './generative/adapter'
import { availableCapability, dispatchUISpecAction } from './generative/actions'
import { boundRecord } from './qualification/generativeLiveData'
import { renderToStaticMarkup } from 'react-dom/server'
import { renderGenerativeUI } from '@assistant-ui/react-generative-ui'
import { styledGenerativeUILibrary } from './generative/vendor/generative-ui'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { UISpecView } from './generative/UISpecView'

describe('twenty-two donor structures integrated with live bindings', () => {
  it('retains the exact gallery order and source provenance', () => {
    expect(donorStructures).toHaveLength(22)
    expect(donorStructures.map((item) => item.slug)).toEqual(Object.keys(generativeActions))
    expect(donorGallerySha256).toMatch(/^[a-f0-9]{64}$/)
    expect(trace.map((item) => item.componentId)).toEqual(Array.from({ length: 22 }, (_, i) => `aui-${129 + i}`))
  })

  it.each(Object.keys(generativeActions) as GenerativeTemplate[])('%s has a producer/consumer trace and cannot render donor sample records', (template) => {
    const spec = bindDonorUISpec(boundRecord(template))
    const row = trace.find((item) => item.slug === template)
    expect(row?.producerContract).toContain('ToolSegment')
    expect(row?.consumerFiles).toContain('features/chat/auiResultRegistry.tsx')
    expect(row?.consumerMount).toBe('pending')
    expect(spec?.tree.$type).toBe(donorStructures.find((item) => item.slug === template)?.tree.$type)
    expect(JSON.stringify(spec?.tree)).not.toContain('images.unsplash.com')
    expect(JSON.stringify(spec?.tree)).not.toContain('ACME')
    expect(requiredBindings(template).length).toBeGreaterThan(0)
  })

  it('rejects a model-authored component, event prop and cross-template action', () => {
    const record = boundRecord('stays')
    const live = bindDonorUISpec(record)!
    expect(parseLiveUISpec({ ...live, tree: { $type: 'iframe' } })).toBeNull()
    expect(parseLiveUISpec({ ...live, tree: { $type: 'Card', onClick: 'buy' } })).toBeNull()
    expect(parseLiveUISpec({ ...live, tree: { $type: 'Button', $action: { type: 'send_email' } } })).toBeNull()
  })

  it('removes every unconnected action from the render tree', () => {
    for (const template of Object.keys(generativeActions) as GenerativeTemplate[]) {
      const spec = bindDonorUISpec(boundRecord(template))!
      const plan = planUISpecRender(spec)
      expect(JSON.stringify(plan.tree)).not.toContain('"$action"')
      if (generativeActions[template].length) expect(plan.unavailable.length).toBeGreaterThan(0)
    }
  })

  it('refuses malformed and unbounded action input before any handler', async () => {
    const spec = bindDonorUISpec(boundRecord('booking'))!
    for (const action of [null, { type: 7 }, { type: 'book_reservation', rogue: 1 },
      { type: 'book_reservation', $input: Array(40).fill(1) },
      { type: 'book_reservation', $input: { value: Infinity } },
      { type: 'book_reservation', $input: { constructor: 'override' } }]) {
      expect((await dispatchUISpecAction(spec, action as never, {})).ok).toBe(false)
    }
  })

  it('propagates provider cancellation, invalid responses and exceptions without a success claim', async () => {
    const spec = bindDonorUISpec(boundRecord('booking'))!
    const base = { kind: 'preview-confirm' as const, producer: spec.producer, recordId: spec.recordId }
    const cancelled = await dispatchUISpecAction(spec, { type: 'book_reservation' }, {
      book_reservation: { ...base, previewAndConfirm: async () => ({ status: 'cancelled', message: 'No booking made.' }) },
    })
    expect(cancelled).toMatchObject({ ok: false, outcome: 'cancelled' })
    const invalid = await dispatchUISpecAction(spec, { type: 'book_reservation' }, {
      book_reservation: { ...base, previewAndConfirm: async () => ({ status: 'unknown' as never, message: '' }) },
    })
    expect(invalid).toMatchObject({ ok: false, outcome: 'failed' })
    const thrown = await dispatchUISpecAction(spec, { type: 'book_reservation' }, {
      book_reservation: { ...base, previewAndConfirm: async () => { throw new Error('Provider disconnected') } },
    })
    expect(thrown).toMatchObject({ ok: false, outcome: 'failed', message: 'Provider disconnected' })
  })

  it('uses the adopted donor Markdown renderer for live content and nested children', () => {
    const rich = renderToStaticMarkup(renderGenerativeUI({ $type: 'Markdown', value: '**Live record**', children: 'Detail' }, styledGenerativeUILibrary))
    expect(rich).toContain('<strong>Live record</strong>')
    expect(rich).toContain('Detail')
    const empty = renderToStaticMarkup(renderGenerativeUI({ $type: 'Markdown', children: 'Only child' }, styledGenerativeUILibrary))
    expect(empty).toContain('Only child')
  })

  it('opens an existing review flow without claiming a task was created', async () => {
    const spec = bindDonorUISpec(boundRecord('create-task'))!
    let received: unknown
    const capability = { create_task: {
      kind: 'open-authorized-flow' as const, producer: spec.producer, recordId: spec.recordId,
      open: (scope: unknown) => { received = scope; return true },
    } }
    const result = await dispatchUISpecAction(spec, { type: 'create_task', $input: { title: 'Live task' } }, capability)
    expect(result).toEqual({ ok: true, outcome: 'opened', message: 'Review flow opened; no change has been made.' })
    expect(received).toEqual({ producer: spec.producer, recordId: spec.recordId, fields: { title: 'Live task' } })
  })

  it('renders an unsupported stay as live data with an unavailable action', () => {
    const spec = bindDonorUISpec(boundRecord('stays'))!
    render(<UISpecView spec={spec} />)
    expect(screen.getByRole('region', { name: 'Live stays result' })).toBeTruthy()
    expect(screen.getByText('Live title')).toBeTruthy()
    expect(screen.getAllByText(/No connected provider or authorized route/).length).toBeGreaterThan(0)
    expect(document.querySelector('[data-aui-action="book_stay"]')).toBeNull()
  })

  it('routes a real donor button through the scoped review callback and reports cancellation', async () => {
    const spec = bindDonorUISpec(boundRecord('stays'))!
    const observed: unknown[] = []
    const capability = { book_stay: {
      kind: 'preview-confirm' as const, producer: spec.producer, recordId: spec.recordId,
      previewAndConfirm: async (scope: unknown) => {
        observed.push(scope)
        return { status: 'cancelled' as const, message: 'No stay booked.' }
      },
    } }
    render(<UISpecView spec={spec} capabilities={capability} />)
    await userEvent.setup().click(screen.getByRole('button', { name: 'Live children.2.label' }))
    expect(observed).toEqual([{ producer: spec.producer, recordId: spec.recordId, fields: {} }])
    expect(screen.getByRole('alert').textContent).toContain('No stay booked.')
  })

  it('validates malformed envelopes, child keys and nested form actions', () => {
    const base = { template: 'stays', producer: 'tool:stays', recordId: 'record-1' }
    expect(parseLiveUISpec(null)).toBeNull()
    expect(parseLiveUISpec({ ...base, template: 'unknown', tree: { $type: 'Card' } })).toBeNull()
    expect(parseLiveUISpec({ ...base, producer: '', tree: { $type: 'Card' } })).toBeNull()
    expect(parseLiveUISpec({ ...base, tree: { $type: 'Card', children: [null] } })).toBeNull()
    expect(parseLiveUISpec({ ...base, tree: { $type: 'Card', children: [{ $type: 'Text', $key: 'x' }, { $type: 'Text', $key: 'x' }] } })).toBeNull()
    expect(parseLiveUISpec({ ...base, tree: { $type: 'Card', confirm: { label: 'Book', $action: { type: 'send_email' } } } })).toBeNull()
    expect(parseLiveUISpec({ ...base, tree: { $type: 'Card', confirm: 'Book' } })).toBeNull()
    expect(parseLiveUISpec({ ...base, tree: { $type: 'Card', $key: '' } })).toBeNull()
    expect(parseLiveUISpec({ ...base, tree: { $type: 'Text', rows: [1] } })).toBeTruthy()
    expect(parseLiveUISpec({ ...base, tree: { $type: 'Text', value: { a: { b: { c: { d: { e: 1 } } } } } } })).toBeNull()
    expect(parseLiveUISpec({ ...base, tree: { $type: 'Card', note: null, enabled: true } })).toBeTruthy()
    expect(requiredBindings('unknown' as GenerativeTemplate)).toEqual([])
  })

  it('rejects missing, malformed and unsafe live data bindings', () => {
    const record = boundRecord('stays')
    expect(bindDonorUISpec(null)).toBeNull()
    expect(bindDonorUISpec({ ...record, bindings: [] })).toBeNull()
    expect(bindDonorUISpec({ ...record, bindings: { ...record.bindings, title: 10 } })).toBeNull()
    expect(bindDonorUISpec({ ...record, bindings: { ...record.bindings, title: null } })).toBeNull()
    expect(bindDonorUISpec({ ...record, bindings: { ...record.bindings, 'children.0.src': 'javascript:alert(1)' } })).toBeNull()
    const absent = { ...record.bindings }
    delete absent.title
    expect(bindDonorUISpec({ ...record, bindings: absent })).toBeNull()
  })

  it('replaces unhandled buttons and actions with visible unavailable notices', () => {
    const base = { template: 'stays', producer: 'tool:stays', recordId: 'record-1' }
    const noAction = parseLiveUISpec({ ...base, tree: { $type: 'Button', label: 'Unknown action' } })!
    expect(planUISpecRender(noAction).unavailable).toEqual(['button'])
    const unnamed = parseLiveUISpec({ ...base, tree: { $type: 'Button' } })!
    expect(planUISpecRender(unnamed).tree.title).toBe('Action unavailable')
    const noLabel = parseLiveUISpec({ ...base, tree: { $type: 'Button', $action: { type: 'book_stay' } } })!
    expect(planUISpecRender(noLabel).tree.title).toBe('Action unavailable')
    const singleChild = parseLiveUISpec({ ...base, tree: { $type: 'Card', children: { $type: 'Button', $action: { type: 'book_stay' }, label: 'Book' } } })!
    expect(Array.isArray(planUISpecRender(singleChild).tree.children)).toBe(true)
  })

  it('keeps a form submit only when its scoped review callback exists', () => {
    const spec = bindDonorUISpec(boundRecord('draft-email'))!
    const unavailable = planUISpecRender(spec)
    expect(unavailable.unavailable).toContain('submit')
    expect(JSON.stringify(unavailable.tree)).not.toContain('"submit":true')
    const capability = { send_email: {
      kind: 'preview-confirm' as const, producer: spec.producer, recordId: spec.recordId,
      previewAndConfirm: async () => ({ status: 'cancelled' as const, message: 'No email sent.' }),
    } }
    expect(JSON.stringify(planUISpecRender(spec, capability).tree)).toContain('"submit":true')
    const booking = bindDonorUISpec(boundRecord('booking'))!
    const reservation = { book_reservation: {
      kind: 'preview-confirm' as const, producer: booking.producer, recordId: booking.recordId,
      previewAndConfirm: async () => ({ status: 'cancelled' as const, message: 'No reservation made.' }),
    } }
    expect(planUISpecRender(booking, reservation).tree.asForm).toBe(true)
  })

  it('rejects invalid form values before invoking an authorized callback', async () => {
    const spec = bindDonorUISpec(boundRecord('booking'))!
    let calls = 0
    const capability = { book_reservation: {
      kind: 'preview-confirm' as const, producer: spec.producer, recordId: spec.recordId,
      previewAndConfirm: async () => { calls++; return { status: 'cancelled' as const, message: 'Cancelled.' } },
    } }
    const cycle: Record<string, unknown> = {}; cycle.self = cycle
    const getter = Object.defineProperty({}, 'title', { enumerable: true, get: () => { throw Error('Getter blocked') } })
    for (const fields of [null, [1], { number: Infinity }, { cycle }, { title: 'x'.repeat(4097) },
      { title: Array(33).fill(1) }, { title: { a: { b: { c: { d: { e: 1 } } } } } }, getter]) {
      expect((await dispatchUISpecAction(spec, { type: 'book_reservation', $input: fields }, capability)).ok).toBe(false)
    }
    expect(calls).toBe(0)
    expect((await dispatchUISpecAction(spec, { type: 'book_reservation', $input: { guests: 2, reminder: false } }, capability)).outcome).toBe('cancelled')
    expect((await dispatchUISpecAction(spec, { type: 'book_reservation', $input: { choices: [1, 2] } }, capability)).outcome).toBe('cancelled')
    expect(calls).toBe(2)
  })

  it('checks template, producer and record scope before opening anything', async () => {
    const spec = bindDonorUISpec(boundRecord('receipt'))!
    const capability = { view_order_details: {
      kind: 'open-record' as const, producer: spec.producer, recordId: spec.recordId,
      open: () => false,
    } }
    expect(availableCapability(spec, 'confirm_purchase', capability)).toBeNull()
    expect((await dispatchUISpecAction(spec, { type: 'view_order_details' }, {})).outcome).toBe('unavailable')
    expect(availableCapability(spec, 'view_order_details', { view_order_details: { ...capability.view_order_details, producer: 'other' } })).toBeNull()
    expect((await dispatchUISpecAction(spec, { type: 'view_order_details' }, capability)).outcome).toBe('unavailable')
    expect((await dispatchUISpecAction(spec, { type: 'view_order_details' }, { view_order_details: { ...capability.view_order_details, open: () => true } })).outcome).toBe('opened')
  })

  it('reports failed review-flow navigation and non-Error provider failures', async () => {
    const spec = bindDonorUISpec(boundRecord('create-task'))!
    const base = { kind: 'open-authorized-flow' as const, producer: spec.producer, recordId: spec.recordId }
    expect((await dispatchUISpecAction(spec, { type: 'create_task' }, { create_task: { ...base, open: () => false } })).outcome).toBe('unavailable')
    expect((await dispatchUISpecAction(spec, { type: 'create_task' }, { create_task: { ...base, open: () => { throw 'offline' } } })).message).toBe('The action failed.')
    expect(availableCapability(spec, 'create_task', { create_task: { kind: 'open-record', producer: spec.producer, recordId: spec.recordId, open: () => true } })).toBeNull()
  })

  it('announces an opened live result without claiming a mutation', async () => {
    const spec = parseLiveUISpec({ template: 'receipt', producer: 'tool:receipt', recordId: 'receipt-1',
      tree: { $type: 'Button', label: 'View receipt', $action: { type: 'view_order_details' } } })!
    const capability = { view_order_details: { kind: 'open-record' as const, producer: spec.producer, recordId: spec.recordId, open: () => true } }
    render(<UISpecView spec={spec} capabilities={capability} />)
    await userEvent.setup().click(screen.getByRole('button', { name: 'View receipt' }))
    expect(screen.getByRole('status').textContent).toContain('Record opened.')
  })
})
