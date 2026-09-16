import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { Row } from './settingsUI'
import { Toggle } from '../../shared/ui/Toggle'


const SRC = join(process.cwd(), "src")
const SETTINGS_UI = 'features/settings/settingsUI.tsx'

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const sources = (): { file: string; src: string }[] =>
  walk(SRC).map((p) => ({ file: relative(SRC, p), src: readFileSync(p, 'utf8') }))

const consumerFiles = (all: { file: string; src: string }[]) =>
  all.filter(({ src }) => /from '(\.|\.\.)+\/(pages\/settings\/)?settingsUI'/.test(src))

function openingTags(src: string, name = 'Row'): string[] {
  const tags: string[] = []
  const re = new RegExp(`<${name}(?=[\\s/>])`, 'g')
  let m: RegExpExecArray | null
  while ((m = re.exec(src))) {
    let i = m.index + name.length + 1
    let depth = 0
    let quote: string | null = null
    for (; i < src.length; i++) {
      const c = src[i]
      if (quote) { if (c === quote) quote = null; continue }
      if (c === '"' || c === "'" || c === '`') { quote = c; continue }
      if (c === '{') { depth++; continue }
      if (c === '}') { depth--; continue }
      if (c === '>' && depth === 0) break
    }
    tags.push(src.slice(m.index, i + 1))
  }
  return tags
}

describe('the settings Row population this invariant protects', () => {
  it('is a real, load-bearing population — not a vacuous scan', () => {
    const consumers = consumerFiles(sources())
    const direct = consumers.flatMap(({ file, src }) => openingTags(src).map((t) => ({ file, t })))
    const viaToggle = consumers.flatMap(({ file, src }) => openingTags(src, 'ToggleRow').map((t) => ({ file, t })))
    const all = [...direct, ...viaToggle]
    const hinted = all.filter(({ t }) => /\bhint=/.test(t))
    const panels = new Set(all.map(({ file }) => file))
    expect(consumers.length, 'files importing from settingsUI').toBeGreaterThan(30)
    expect(direct.length, 'direct <Row> call sites — if this collapses, the tag scanner broke').toBeGreaterThan(60)
    expect(viaToggle.length, '<ToggleRow> call sites, each a Row on screen').toBeGreaterThan(15)
    expect(panels.size, 'panels rendering a settings row').toBeGreaterThan(18)
    expect(hinted.length, 'rows passing a hint: the rows whose control could drift').toBeGreaterThan(80)
    expect(hinted.length / all.length, 'a hinted row is the normal row, not the outlier').toBeGreaterThan(0.85)
  })

  it('the tag scanner survives a `>` inside an attribute', () => {
    const tags = openingTags('<Row onChange={(v) => f(v)} right={<Chip a={1}>hi</Chip>} hint="h" label="l">x</Row>')
    expect(tags).toHaveLength(1)
    expect(tags[0], 'the whole tag, including the hint that follows a nested `>`').toContain('hint="h"')
    expect(tags[0]).toContain('label="l"')
    expect(openingTags('<RowGroup><Row label="a">x</Row></RowGroup>'), 'RowGroup is not a Row').toHaveLength(1)
    expect(openingTags('<ToggleRow label="a" />', 'ToggleRow')).toHaveLength(1)
  })
})

describe('a settings Row puts its control on the label\'s row', () => {
  const renderRow = (hint?: string) => {
    const { container } = render(<Row label="Encrypt shards" hint={hint}><Toggle on={false} onChange={() => {}} label="Encrypt shards" /></Row>)
    const row = container.querySelector('div.grid') as HTMLElement
    expect(row, 'Row must render a grid container').not.toBeNull()
    return { row, kids: [...row.children] as HTMLElement[] }
  }

  it('is a two-column grid whose first column can shrink', () => {
    const { row } = renderRow('a hint')
    const cls = row.className
    expect(cls, 'two columns: shrinkable label, auto control').toContain('grid-cols-[minmax(0,1fr)_auto]')
    expect(cls, 'each grid ROW centres its own items').toContain('items-center')
    expect(cls, 'a flex row is the shape this replaced').not.toMatch(/(^|\s)flex(\s|$)/)
    expect(cls, 'justify-between belongs to the flex shape').not.toContain('justify-between')
    expect(cls, 'items-start is not the fix — it top-aligns instead of centring on the label').not.toContain('items-start')
  })

  it('places the control in column 2 of the LABEL\'s row, and the hint below it', () => {
    const { kids } = renderRow('a hint that would wrap on a phone')
    expect(kids, 'label, hint, control — DOM order unchanged').toHaveLength(3)
    const [label, hintEl, slot] = kids
    expect(label.textContent).toBe('Encrypt shards')
    expect(hintEl.id, 'the hint keeps its id, so a control that claims it still can').toBeTruthy()
    expect(slot.className, 'control pinned to row 1, column 2').toContain('col-start-2 row-start-1')
    expect(label.className, 'the label must AUTO-place, so it shares row 1 with the control').not.toMatch(/row-start-/)
    expect(hintEl.className, 'the hint must AUTO-place, so it falls to row 2').not.toMatch(/row-start-|col-start-/)
    expect(slot.className, 'the slot height must be the control height, not a line box').toContain('flex items-center')
    expect(slot.children, 'all children share ONE slot — see oneControlPerRow').toHaveLength(1)
  })

  it('still works with no hint at all', () => {
    const { kids } = renderRow(undefined)
    expect(kids, 'label, control').toHaveLength(2)
    expect(kids[1].className).toContain('col-start-2 row-start-1')
  })
})

describe('the fix is not half-applied', () => {
  it('no NEW hand-rolled copy of the old flex row appears in the tree', () => {
    const OLD = /flex items-(start|center) justify-between gap-l border-b border-outline-variant\/30 py-[23] last:border-0/g
    const hits = sources().flatMap(({ file, src }) => (src.match(OLD) ?? []).map(() => file))
    expect(sources().length, 'the walk must find the tree').toBeGreaterThan(300)
    expect(hits.length, 'a pattern matching nothing looks identical to a fixed tree').toBeGreaterThan(0)
    expect(hits.sort(), 'record rows carrying the old container string').toEqual([
      'features/settings/DevicesPanel.tsx',
      'features/settings/GuardrailsPanel.tsx',
      'features/settings/GuardrailsPanel.tsx',
    ])
    expect(hits, 'the primitive itself must not hold the old shape').not.toContain(SETTINGS_UI)
  })

  it('Row is the only label-left/control-right settings row primitive', () => {
    const decls = sources().filter(({ src }) => /^export function Row\(/m.test(src)).map(({ file }) => file)
    expect(decls).toEqual([SETTINGS_UI])
  })
})
