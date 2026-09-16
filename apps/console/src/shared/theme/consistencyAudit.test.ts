import { describe, it, expect } from 'vitest'
import { buildAuditPayload, scanDrift } from './consistencyAudit.report'


describe('consistency-audit: drift reporter (measure only, never fails on drift)', () => {
  it('scans a meaningful number of source files', () => {
    expect(scanDrift().totals.filesScanned).toBeGreaterThan(100)
  })

  it('builds a well-formed drift inventory', () => {
    const payload = buildAuditPayload()
    expect(payload.byCategory).toHaveProperty('color')
    expect(Array.isArray(payload.ranked)).toBe(true)
    // Global a11y safety nets must be present (they're the app-wide coverage).
    expect(payload.a11y.hasGlobalReducedMotion).toBe(true)
    expect(payload.a11y.hasGlobalFocusRing).toBe(true)
  })

  it(
    'is a pure function of the tree, so two runs agree',
    () => {
      expect(JSON.stringify(buildAuditPayload())).toBe(JSON.stringify(buildAuditPayload()))
    },
    120_000,
  )
})
