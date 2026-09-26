import { describe, expect, it } from 'vitest'
import { bindDonorUISpec, generativeActions, requiredBindings } from '../generative/uispec'
import { planUISpecRender } from '../generative/adapter'
import { dispatchUISpecAction } from '../generative/actions'
import { boundRecord } from './generativeLiveData'

const templates = ['chart-line', 'create-task', 'software-purchase', 'chart-bars'] as const

describe('part 04 live donor structures', () => {
  it.each(templates)('%s retains the donor structure without demo records', (template) => {
    const record = boundRecord(template)
    const spec = bindDonorUISpec(record)
    expect(spec?.template).toBe(template)
    expect(requiredBindings(template).length).toBeGreaterThan(0)
    expect(JSON.stringify(spec?.tree)).toContain('Live ')
    expect(bindDonorUISpec({ ...record, bindings: { ...record.bindings, injected: true } })).toBeNull()
  })

  it('keeps response time and support tickets read-only', () => {
    for (const template of ['chart-line', 'chart-bars'] as const) {
      const spec = bindDonorUISpec(boundRecord(template))!
      expect(generativeActions[template]).toEqual([])
      expect(planUISpecRender(spec).unavailable).toEqual([])
    }
  })

  it('requires a provider-backed preview for task creation and purchase', async () => {
    for (const [template, action] of [['create-task', 'create_task'], ['software-purchase', 'confirm_purchase']] as const) {
      const spec = bindDonorUISpec(boundRecord(template))!
      expect(planUISpecRender(spec).unavailable).toContain(action)
      expect((await dispatchUISpecAction(spec, { type: action }, {})).outcome).toBe('unavailable')
    }
  })

  it('rejects model-supplied action arguments and unbounded form fields', async () => {
    const spec = bindDonorUISpec(boundRecord('create-task'))!
    expect((await dispatchUISpecAction(spec, { type: 'create_task', taskId: 'injected' } as never, {})).outcome).toBe('unavailable')
    expect((await dispatchUISpecAction(spec, { type: 'create_task', $input: { title: 'x'.repeat(5000) } }, {})).outcome).toBe('unavailable')
  })

  it('propagates a provider refusal without claiming purchase success', async () => {
    const spec = bindDonorUISpec(boundRecord('software-purchase'))!
    const capability = { confirm_purchase: {
      kind: 'preview-confirm' as const, producer: spec.producer, recordId: spec.recordId,
      previewAndConfirm: async () => ({ status: 'failed' as const, message: 'Purchase provider unavailable.' }),
    } }
    expect(await dispatchUISpecAction(spec, { type: 'confirm_purchase' }, capability))
      .toEqual({ ok: false, outcome: 'failed', message: 'Purchase provider unavailable.' })
  })
})
