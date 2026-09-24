import { renderToStaticMarkup } from 'react-dom/server'
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import type { KnowledgeConflict } from '../../shared/data/api'
import { ConflictRow } from './ConflictPanel'

const conflict: KnowledgeConflict = {
  item_id: 'new', item_title: 'Current measurement',
  left_claim: 'Cold start latency is 9.1 seconds',
  right_claim: 'Cold start latency is 4.2 seconds',
  left_item: 'new', right_item: 'stored', kind: 'number',
  prefer: '', detail: '9.1 seconds vs 4.2 seconds', confidence: 1,
}

describe('deterministic conflict presentation', () => {
  it('renders a provable conflict with both claims and source precedence', () => {
    const markup = renderToStaticMarkup(<ConflictRow conflict={conflict} />)
    expect(markup).toContain('Provable conflict')
    expect(markup).toContain(conflict.left_claim)
    expect(markup).toContain(conflict.right_claim)
    expect(markup).toContain('Both sources carry the same weight')
    expect(markup).not.toMatch(/Possible conflict|% confident/)
  })

  it('does not branch on an obsolete tier in persisted payloads', () => {
    const legacy = { ...conflict, basis: 'model', confidence: 0.6 }
    const markup = renderToStaticMarkup(<ConflictRow conflict={legacy} />)
    expect(markup).toContain('Provable conflict')
    expect(markup).not.toMatch(/Possible conflict|% confident/)
  })

  it('removes the tier from the frontend contract and panel', () => {
    const api = readFileSync('src/shared/data/api.ts', 'utf8')
    const contract = api.split('export type KnowledgeConflict = {')[1].split('}')[0]
    expect(contract).not.toMatch(/\bbasis\b/)
    const panel = readFileSync('src/features/knowledge/ConflictPanel.tsx', 'utf8')
    expect(panel).not.toMatch(/\bbasis\b|Possible conflict|Sparkles/)
  })
})
