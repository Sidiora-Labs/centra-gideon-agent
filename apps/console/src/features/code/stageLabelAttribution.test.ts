import { describe, expect, it } from 'vitest'
import { normalizeStageLabel } from './CodeCockpitPage'

describe('normalizeStageLabel', () => {
  it('strips model-authored numeric decoration and folds case', () => {
    expect(normalizeStageLabel('Stage 2/3 — BUILD ALL MODULES')).toBe('build all modules')
    expect(normalizeStageLabel('1. Tests & QA')).toBe('tests & qa')
  })
})
