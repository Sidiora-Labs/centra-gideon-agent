import { describe, expect, it } from 'vitest'
import { bindDonorUISpec, generativeActions, requiredBindings } from '../generative/uispec'
import { planUISpecRender } from '../generative/adapter'
import { dispatchUISpecAction } from '../generative/actions'
import { boundRecord } from './generativeLiveData'

const templates = ['view-event', 'weather-current', 'ride-status', 'draft-email', 'cart', 'playlist'] as const

describe('part 02 live donor structures', () => {
  it.each(templates)('%s keeps its donor tree and requires live payload values', (template) => {
    const record = boundRecord(template)
    const spec = bindDonorUISpec(record)
    expect(spec?.template).toBe(template)
    expect(spec?.recordId).toBe(record.recordId)
    expect(requiredBindings(template).length).toBeGreaterThan(0)
    expect(JSON.stringify(spec?.tree)).toContain('Live ')
    expect(bindDonorUISpec({ ...record, bindings: {} })).toBeNull()
  })

  it('keeps event, weather and ride data read-only', () => {
    for (const template of ['view-event', 'weather-current', 'ride-status'] as const) {
      const spec = bindDonorUISpec(boundRecord(template))!
      expect(generativeActions[template]).toEqual([])
      expect(planUISpecRender(spec).unavailable).toEqual([])
    }
  })

  it('refuses send-email and purchase without their specific authorized flow', async () => {
    for (const [template, action] of [['draft-email', 'send_email'], ['cart', 'purchase_cart']] as const) {
      const spec = bindDonorUISpec(boundRecord(template))!
      expect(planUISpecRender(spec).unavailable).toContain(action)
      expect((await dispatchUISpecAction(spec, { type: action }, {})).outcome).toBe('unavailable')
    }
  })

  it('removes the draft email submit control when sending has no provider', () => {
    const spec = bindDonorUISpec(boundRecord('draft-email'))!
    const plan = planUISpecRender(spec)
    expect(plan.unavailable).toContain('send_email')
    expect(plan.unavailable).toContain('submit')
    expect(JSON.stringify(plan.tree)).not.toContain('"submit":true')
  })

  it('does not accept a real handler scoped to a different email record', async () => {
    const spec = bindDonorUISpec(boundRecord('draft-email'))!
    const capability = { send_email: {
      kind: 'preview-confirm' as const, producer: spec.producer, recordId: 'another-record',
      previewAndConfirm: async () => ({ status: 'confirmed' as const, message: 'Sent' }),
    } }
    expect((await dispatchUISpecAction(spec, { type: 'send_email' }, capability)).outcome).toBe('unavailable')
  })

  it('allows a nonmutating playlist route only when a real scoped opener reports success', async () => {
    const spec = bindDonorUISpec(boundRecord('playlist'))!
    let opened = ''
    const capability = { view_playlist: {
      kind: 'open-record' as const, producer: spec.producer, recordId: spec.recordId,
      open: ({ recordId }: { recordId: string }) => { opened = recordId; return true },
    } }
    expect((await dispatchUISpecAction(spec, { type: 'view_playlist' }, capability)).outcome).toBe('opened')
    expect(opened).toBe(spec.recordId)
    expect((await dispatchUISpecAction(spec, { type: 'play_track' }, capability)).outcome).toBe('unavailable')
  })
})
