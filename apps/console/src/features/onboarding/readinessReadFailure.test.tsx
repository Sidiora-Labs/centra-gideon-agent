import { describe, expect, it } from 'vitest'
import { modelCheckVerdict } from './essentialSetupState'

describe('model readiness verdicts', () => {
  it('keeps unreadable, unset and ready outcomes distinct', () => {
    const unknown = modelCheckVerdict({ ok: false, code: 'read_failed', what: 'Check could not run', why: 'Binding record unreadable', fix: 'Retry' })
    expect(unknown).toEqual({ kind: 'unknown', message: 'Check could not run. Binding record unreadable. Retry' })
    expect(modelCheckVerdict({ ok: false, code: 'model_unresolved', what: 'No model', why: 'No provider configured', fix: 'Choose a model' }).kind).toBe('refused')
    expect(modelCheckVerdict({ ok: true, source: 'binding', bound: ['local:m'], provider: 'local', model: 'm', local: true })).toEqual({ kind: 'ok', model: 'local:m' })
  })
})
