import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const FILE = join(process.cwd(), "src/features/knowledge/KnowledgeDetail.tsx")

function code(): string {
  return readFileSync(FILE, 'utf8')
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^[ \t]*\/\/.*$/gm, '')
}

function metadataRow(src: string): { row: string; wrapper: string } {
  const strip = src.indexOf('<ProcessingStrip')
  expect(strip, 'ProcessingStrip is no longer rendered here — this rail is measuring nothing').toBeGreaterThan(-1)
  const before = src.slice(0, strip)
  const wrappers = [...before.matchAll(/<div className="[^"]*\bml-auto\b[^"]*">/g)]
  const rows = [...before.matchAll(/<div className="[^"]*\bitems-start\b[^"]*">/g)]
  expect(wrappers.length, 'no ml-auto wrapper found before ProcessingStrip').toBeGreaterThan(0)
  expect(rows.length, 'no items-start row found before ProcessingStrip').toBeGreaterThan(0)
  const wrapper = wrappers[wrappers.length - 1][0]
  const row = rows[rows.length - 1][0]
  expect(row).not.toBe(wrapper)
  expect(row, `resolved the wrong element as the row: ${row}`).not.toMatch(/\bitems-center\b/)
  return { wrapper, row }
}

describe('knowledge item detail — metadata row must survive a phone width', () => {
  it('wraps, so the ingestion strip can drop to its own line instead of being clipped', () => {
    const { row } = metadataRow(code())
    expect(row, `the row holding the metadata + ProcessingStrip must be able to wrap: ${row}`)
      .toMatch(/\bflex-wrap\b/)
  })

  it('lets the strip shrink so its own stage wrapping can engage', () => {
    const { wrapper } = metadataRow(code())
    expect(wrapper, `the ProcessingStrip wrapper must not be pinned to max-content: ${wrapper}`)
      .not.toMatch(/\bshrink-0\b/)
  })

  it('still right-aligns the strip on a wide row', () => {
    const { wrapper } = metadataRow(code())
    expect(wrapper).toMatch(/\bml-auto\b/)
  })

  it('the stripped source still contains real JSX (guard against a vacuous pass)', () => {
    const src = code()
    expect(src).toContain('<ProcessingStrip')
    expect(src).toMatch(/className="flex flex-wrap shrink-0 items-start/)
    expect(src).not.toContain('measured 422px')
  })
})
