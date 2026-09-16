import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const src = readFileSync(join(__dirname, "settingsWidgets.tsx"), 'utf-8')

describe('the Models tile speaks when nothing is bound', () => {
  it('renders the sibling-idiom empty sentence for the all-unbound case', () => {
    expect(src).toContain(
      'No models bound yet. Set up a model provider and the bindings for chat, embeddings, and voice appear here.',
    )
  })

  it('the empty sentence is gated on anyBound so a partial binding keeps the KVList', () => {
    const modelsTile = src.slice(src.indexOf("id: 'models'"), src.indexOf("id: 'routing'"))
    expect(modelsTile, 'the tile still derives anyBound from the CORE use cases').toMatch(/anyBound/)
    expect(modelsTile, 'partial bindings still render the KVList').toContain('anyBound ? <KVList')
    expect(modelsTile, 'the per-row dash for a partially-bound list survives').toContain("vText: bound ? shortModel(bound) : '—'")
  })
})
