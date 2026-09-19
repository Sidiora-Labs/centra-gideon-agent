import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { partitionRunHistory } from '../../shared/data/api'

describe('suppressed run history', () => {
  it('partitions exact skipped outcomes without dropping or reordering rows', () => {
    const rows = [
      { id: 'done', status: 'done' },
      { id: 'branch', status: 'skipped' },
      { id: 'quiet', status: 'skipped_quiet' },
      { id: 'failure', status: 'failed' },
      { id: 'similar', status: 'not_skipped' },
    ]

    const partitioned = partitionRunHistory(rows, (row) => row.status)

    expect(partitioned.visible.map((row) => row.id)).toEqual(['done', 'failure', 'similar'])
    expect(partitioned.suppressed.map((row) => row.id)).toEqual(['branch', 'quiet'])
  })

  it('keeps both disclosures connected to their controlled rows', () => {
    for (const file of ['WorkflowRunDetail.tsx', '../triggers/TriggersListPage.tsx']) {
      const source = readFileSync(resolve(__dirname, file), 'utf8')
      expect(source).toContain('aria-controls={runHistoryId}')
      expect(source).toContain('aria-expanded={showSuppressed}')
      expect(source).toMatch(/Show \$\{partitioned(?:Rows|Triggers)\.suppressed\.length\} suppressed/)
      expect(source).toMatch(/Hide \$\{partitioned(?:Rows|Triggers)\.suppressed\.length\} suppressed/)
    }
  })
})
