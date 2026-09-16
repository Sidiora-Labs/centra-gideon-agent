
export const UNRECORDED = 'unrecorded'

export const UNRECORDED_LABEL = 'not recorded'

export const PROVENANCE_SCHEMA = 2

export function reportSchema(report: { report_schema?: number } | null | undefined): number | null {
  const raw = report?.report_schema
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : null
}

export function provenanceRecorded(
  report: { report_schema?: number } | null | undefined,
): boolean {
  const schema = reportSchema(report)
  return schema !== null && schema >= PROVENANCE_SCHEMA
}

export function tokensUnrecorded(row: { tokens_recorded?: boolean }): boolean {
  return row.tokens_recorded === false
}
