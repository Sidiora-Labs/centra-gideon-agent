import { describe, it, expect } from 'vitest'
import { selectedImportFingerprints } from './importSetupState'
import { summaryOfReport } from './ImportStep'
import type { OnboardingImportItem, OnboardingImportReport } from '../../shared/data/api'

const items: OnboardingImportItem[] = ['instructions', 'mcp_servers', 'mcp_servers', 'skills'].map((category, index) => ({
  fingerprint: String(index + 1).padStart(16, '0'), source: 'claude_code', category,
  key: ['CLAUDE.md', 'weather', 'github', 'tidy-notes'][index], title: ['CLAUDE.md', 'weather', 'github', 'tidy-notes'][index],
  redactions: index === 0 ? 1 : 0, existing: false, state: 'new', preselected: true,
}))

describe('the item choice', () => {
  it('sends exactly the chosen fingerprints and nothing else', () => {
    const selected = selectedImportFingerprints(items, { [items[1].fingerprint]: true })
    expect({ fingerprints: selected }).toEqual({ fingerprints: [items[1].fingerprint] })
    expect(JSON.stringify({ fingerprints: selected })).not.toContain('weather')
    expect(JSON.stringify({ fingerprints: selected })).not.toContain('claude_code')
  })
  it('keeps an individual choice across hundreds of rows and deduplicates it', () => {
    const many = Array.from({ length: 600 }, (_, index) => ({ ...items[0], fingerprint: index.toString(16).padStart(16, '0') }))
    const chosen = many[503].fingerprint
    expect(selectedImportFingerprints([...many, many[503]], { [chosen]: true })).toEqual([chosen])
  })
  it('never chooses non-writable destination states', () => {
    const rows = items.map((item, index) => ({ ...item, state: (['new', 'existing', 'conflict', 'rejected'] as const)[index] }))
    expect(selectedImportFingerprints(rows, Object.fromEntries(rows.map(item => [item.fingerprint, true])))).toEqual([rows[0].fingerprint])
  })
  it('reports every destination outcome', () => {
    const report: OnboardingImportReport = { counts: { imported: 1, existing: 1, conflict: 1, rejected: 1 }, results: [], notes: [], secrets_skipped: 0, redactions: 0 }
    expect(summaryOfReport(report)).toBe('1 imported · 1 already there · 1 to review · 1 refused')
  })
})
