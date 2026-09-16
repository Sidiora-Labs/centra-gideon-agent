
import { describe, it, expect } from 'vitest'
import { writeFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { AUDIT_JSON_PATH, buildAuditPayload } from './consistencyAudit.report'

export const WRITE_ENV = 'GIDEON_WRITE_AUDIT'

describe('consistency-audit: inventory generator', () => {
  it.runIf(process.env[WRITE_ENV] === '1')('writes the committed inventory', () => {
    const out = join(process.cwd(), ...AUDIT_JSON_PATH)
    mkdirSync(dirname(out), { recursive: true })
    const payload = buildAuditPayload()
    writeFileSync(out, JSON.stringify(payload, null, 2) + '\n', 'utf8')
    expect(payload.totals.filesScanned).toBeGreaterThan(100)
    expect(Array.isArray(payload.ranked)).toBe(true)
  })
})
