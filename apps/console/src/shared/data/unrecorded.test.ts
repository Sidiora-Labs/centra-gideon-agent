import { describe, expect, it } from 'vitest'
import {
  PROVENANCE_SCHEMA,
  UNRECORDED,
  UNRECORDED_LABEL,
  provenanceRecorded,
  reportSchema,
  tokensUnrecorded,
} from './unrecorded'

describe('the one vocabulary for "unrecorded"', () => {
  it('reads the schema the report STATES and never defaults it to v1', () => {
    expect(reportSchema({ report_schema: 2 })).toBe(2)
    expect(reportSchema({ report_schema: 1 })).toBe(1)
    expect(reportSchema({})).toBeNull()
    expect(reportSchema(undefined)).toBeNull()
    expect(reportSchema(null)).toBeNull()
    expect(reportSchema({ report_schema: NaN })).toBeNull()
  })

  it('answers provenance from the schema, not from whether a key is present', () => {
    expect(provenanceRecorded({ report_schema: PROVENANCE_SCHEMA })).toBe(true)
    expect(provenanceRecorded({ report_schema: PROVENANCE_SCHEMA + 1 })).toBe(true)
    expect(provenanceRecorded({ report_schema: PROVENANCE_SCHEMA - 1 })).toBe(false)
    expect(provenanceRecorded({})).toBe(false)
    expect(provenanceRecorded({ report_schema: 2, provider_binding: null } as never)).toBe(true)
    expect(provenanceRecorded({ report_schema: 1, provider_binding: null } as never)).toBe(false)
  })

  it('treats an ABSENT tokens_recorded as unrecorded, and only an explicit false as the fact', () => {
    expect(tokensUnrecorded({ tokens_recorded: false })).toBe(true)
    expect(tokensUnrecorded({ tokens_recorded: true })).toBe(false)
    expect(tokensUnrecorded({})).toBe(false)
  })

  it('keeps "not recorded" distinct from the panels\' "not measured"', () => {
    expect(UNRECORDED_LABEL).toBe('not recorded')
    expect(UNRECORDED_LABEL).not.toBe('not measured')
    expect(UNRECORDED).toBe('unrecorded')
    expect(PROVENANCE_SCHEMA).toBe(2)
  })
})
