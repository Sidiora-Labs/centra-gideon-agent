import { describe, expect, it } from 'vitest'
import { bindDonorUISpec, generativeActions, requiredBindings } from '../generative/uispec'
import { planUISpecRender } from '../generative/adapter'
import { dispatchUISpecAction } from '../generative/actions'
import { boundRecord } from './generativeLiveData'

const templates = ['channel-message', 'receipt', 'chart-area', 'player-card', 'event-session', 'notify-confirm'] as const

describe('part 03 live donor structures', () => {
  it.each(templates)('%s requires the complete typed result record', (template) => {
    const record = boundRecord(template)
    const spec = bindDonorUISpec(record)
    expect(spec?.template).toBe(template)
    expect(spec?.producer).toBe(record.producer)
    expect(requiredBindings(template).length).toBeGreaterThan(0)
    expect(bindDonorUISpec({ ...record, recordId: '' })).toBeNull()
  })

  it('keeps channel messages, charts and player cards nonmutating', () => {
    for (const template of ['channel-message', 'chart-area', 'player-card'] as const) {
      const spec = bindDonorUISpec(boundRecord(template))!
      expect(generativeActions[template]).toEqual([])
      expect(planUISpecRender(spec).unavailable).toEqual([])
    }
  })

  it('does not mistake a receipt detail route for a purchase provider', async () => {
    const spec = bindDonorUISpec(boundRecord('receipt'))!
    expect((await dispatchUISpecAction(spec, { type: 'confirm_purchase' }, {})).outcome).toBe('unavailable')
    expect((await dispatchUISpecAction(spec, { type: 'view_order_details' }, {})).outcome).toBe('unavailable')
  })

  it('cannot confirm a destructive dialog through a read-only callback', async () => {
    const spec = bindDonorUISpec(boundRecord('notify-confirm'))!
    const capability = { confirm_delete: {
      kind: 'open-record' as const, producer: spec.producer, recordId: spec.recordId,
      open: () => true,
    } }
    expect(planUISpecRender(spec, capability).unavailable).toContain('confirm_delete')
    expect((await dispatchUISpecAction(spec, { type: 'confirm_delete' }, capability)).ok).toBe(false)
  })

  it('keeps speaker navigation scoped to the conference session record', async () => {
    const spec = bindDonorUISpec(boundRecord('event-session'))!
    const capability = { view_speaker: {
      kind: 'open-record' as const, producer: spec.producer, recordId: spec.recordId,
      open: ({ recordId }: { recordId: string }) => recordId === spec.recordId,
    } }
    expect((await dispatchUISpecAction(spec, { type: 'view_speaker' }, capability)).outcome).toBe('opened')
    expect(planUISpecRender(spec, capability).unavailable).toEqual([])
  })
})
