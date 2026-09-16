import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const FILE = join(process.cwd(), "src/features/settings/ProjectionRulesPanel.tsx")
const src = () =>
  readFileSync(FILE, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

function tagWithLabel(source: string, label: string): string {
  const at = source.indexOf(`aria-label="${label}"`)
  if (at < 0) return ''
  const start = source.lastIndexOf('<', at)
  let depth = 0
  for (let i = start; i < source.length; i++) {
    const c = source[i]
    if (c === '{') depth++
    else if (c === '}') depth--
    else if (c === '>' && depth === 0) return source.slice(start, i + 1)
  }
  return ''
}

describe('the rule-name fields cannot be squeezed out of existence', () => {
  it('neither name input may collapse to zero width', () => {
    const source = src()
    for (const label of ['New rule name', 'Rule name']) {
      const tag = tagWithLabel(source, label)
      expect(tag, `${label} must still exist`).not.toBe('')
      expect(tag, `${label} still permits collapse to 0 — it measured 16px at 390px`)
        .not.toMatch(/\bmin-w-0\b/)
      expect(tag, `${label} needs a usable floor`).toMatch(/\bmin-w-40\b/)
      expect(tag, `${label} should still take the free space`).toMatch(/\bflex-1\b/)
    }
  })

  it('both rows may wrap, so the select drops instead of starving the field', () => {
    const source = src()
    for (const icon of ['<Plus size={13}', '<Scissors size={13}']) {
      const at = source.indexOf(icon)
      expect(at, `${icon} must still open its row`).toBeGreaterThan(-1)
      const rowOpen = source.lastIndexOf('<div className="flex', at)
      const rowTag = source.slice(rowOpen, source.indexOf('>', rowOpen) + 1)
      expect(rowTag, `the row holding ${icon} must be allowed to wrap`).toMatch(/\bflex-wrap\b/)
    }
  })

  it('the strategy select shrinks rather than overflowing its line', () => {
    const source = src()
    const at = source.indexOf('aria-label={forRule ?')
    expect(at, 'the StrategyPicker select must still be here').toBeGreaterThan(-1)
    const tag = source.slice(source.lastIndexOf('<select', at), source.indexOf('>', at) + 1)
    expect(tag).toMatch(/\bmin-w-0\b/)
    expect(tag).toMatch(/\bmax-w-full\b/)
  })

  it('the select and Remove wrap as ONE unit, and that unit does not compete for the line', () => {
    const source = src()
    const at = source.indexOf('aria-label={rule.name ? `Remove rule')
    expect(at, 'the Remove button must still be here').toBeGreaterThan(-1)
    const groupOpen = source.lastIndexOf('<div className="flex', at)
    const groupTag = source.slice(groupOpen, source.indexOf('>', groupOpen) + 1)
    expect(groupTag, 'Remove must sit in a group WITH the select').toMatch(/min-w-0/)
    expect(groupTag, 'and that group must not claim the line the field needs').not.toMatch(/\bflex-1\b/)
    const selectAt = source.indexOf('<StrategyPicker value={shown.strategy}')
    expect(selectAt).toBeGreaterThan(groupOpen)
    expect(selectAt).toBeLessThan(at)
  })

  it('the regex row is deliberately NOT changed', () => {
    const source = src()
    const tag = tagWithLabel(source, 'Match regex for the new rule')
    expect(tag).not.toBe('')
    expect(tag, 'this one legitimately keeps min-w-0').toMatch(/\bmin-w-0\b/)
  })
})
