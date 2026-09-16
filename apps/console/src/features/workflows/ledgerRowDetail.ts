
export interface LedgerRowDetail {
  kind: string
  sha: string
  impact: string
  rationale: string
}

function str(row: Record<string, unknown>, key: string): string {
  const v = row[key]
  return typeof v === 'string' ? v.trim() : ''
}

export function ledgerRowDetail(row: Record<string, unknown>): LedgerRowDetail {
  return {
    kind: str(row, 'kind') || 'event',
    sha: str(row, 'sha'),
    impact: str(row, 'impact'),
    rationale: str(row, 'rationale'),
  }
}

export function ledgerRowKey(row: Record<string, unknown>, index: number): string {
  const id = str(row, 'event_id')
  return id || `row-${index}`
}
