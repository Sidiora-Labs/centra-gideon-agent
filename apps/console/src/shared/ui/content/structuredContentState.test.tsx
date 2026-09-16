import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ApiError, type DeckModelJson, type DocumentLossReport, type SheetModelJson } from '../../data/api'
import { StructuredEditorWorkspace, structuredSaveError } from './structuredEditorState'
import { SheetCells } from './SheetGrid'
import { SlideOutline } from './SlideDeck'
import { InfographicView } from './InfographicView'
import { INFOGRAPHIC_FONT_FAMILIES, loadInfographicEngine } from './antvEngine'
import { asFormula, asLiteral, columnLabel, emptyCell, parseEntry, withCell } from './sheetModelEdit'
import { emptySlide, layoutOptions, slideLabel, slideSizeKey, withAppendedBullet, withAppendedSlide, withInheritedBoxes, withSlideSize, withoutSlide } from './deckModelEdit'

const clean: DocumentLossReport = { lossless: true, kinds: [], items: [], summary: 'Nothing was lost.' }
const sheet: SheetModelJson = { sheets: [{ name: 'Plan', cells: [[{ ...emptyCell(), value: 4 }], [emptyCell(), { ...emptyCell(), formula: '=A1*2' }]], column_widths: [18, 24], merges: ['A1:B1'], frozen_header: true }] }
const deck: DeckModelJson = { title: 'Roadmap', width_in: 12, height_in: 7.5, slides: [{ ...emptySlide(), title: 'Next', bullets: [{ text: 'First', level: 2 }], notes: 'Notes', title_box: { left_in: 0, top_in: 0, width_in: 5, height_in: 1 } }, { ...emptySlide(), title: 'Later' }] }
function workspace<Model>(model: Model, loss = clean) {
  const owner = new StructuredEditorWorkspace<Model>()
  const generation = owner.open('asset')
  owner.receive(generation, { model, loss, version: 4 })
  return owner
}

describe('revisioned structured editing', () => {
  it('rejects stale reads and errors after switching assets', () => {
    const owner = new StructuredEditorWorkspace<SheetModelJson>()
    const stale = owner.open('old')
    const current = owner.open('current')
    owner.receive(stale, { model: sheet, loss: clean, version: 1 })
    owner.reject(stale, new Error('stale request'))
    expect(owner.snapshot().model).toBeNull()
    expect(owner.snapshot().loadError).toBe('')
    owner.receive(current, { model: sheet, loss: clean, version: 4 })
    expect(owner.snapshot().model).toBe(sheet)
    expect(owner.snapshot().slug).toBe('current')
  })
  it('blocks model changes and saving until lossy acknowledgement', () => {
    const owner = workspace(sheet, { ...clean, lossless: false })
    owner.edit(model => withCell(model, 0, 0, 0, { ...emptyCell(), value: 9 }))
    expect(owner.snapshot().model).toBe(sheet)
    expect(owner.beginSave()).toBeNull()
    owner.acknowledge()
    owner.edit(model => withCell(model, 0, 0, 0, { ...emptyCell(), value: 9 }))
    expect(owner.beginSave()?.baseline.version).toBe(4)
  })
  it('takes one save ticket and retains edits made while that snapshot is pending', () => {
    const owner = workspace(deck)
    owner.edit(model => ({ ...model, title: 'First edit' }))
    const ticket = owner.beginSave()!
    expect(owner.beginSave()).toBeNull()
    owner.edit(model => ({ ...model, title: 'Second edit' }))
    owner.finish(ticket, { version: 5 })
    expect(owner.snapshot().baseline?.model.title).toBe('First edit')
    expect(owner.snapshot().model?.title).toBe('Second edit')
    expect(owner.snapshot().saving).toBe(false)
    const next = owner.beginSave()!
    expect(next.baseline.version).toBe(5)
    expect(next.model.title).toBe('Second edit')
    owner.finish(next, { version: 6 })
    expect(owner.beginSave()).toBeNull()
  })
  it('releases cancelled confirmation and reports errors without discarding a draft', () => {
    const owner = workspace(deck)
    owner.edit(model => ({ ...model, title: 'Draft' }))
    owner.finish(owner.beginSave()!, null)
    expect(owner.snapshot().saving).toBe(false)
    owner.finish(owner.beginSave()!, { error: 'Permission denied' })
    expect(owner.snapshot().saveError).toBe('Permission denied')
    expect(owner.snapshot().model?.title).toBe('Draft')
    owner.clearError()
    expect(owner.snapshot().saveError).toBe('')
  })
  it('does not let an old save release or rebase a new asset’s pending save', () => {
    const owner = workspace(deck)
    owner.edit(model => ({ ...model, title: 'Old draft' }))
    const oldTicket = owner.beginSave()!
    const generation = owner.open('new')
    owner.receive(generation, { model: deck, loss: clean, version: 10 })
    owner.edit(model => ({ ...model, title: 'New draft' }))
    const currentTicket = owner.beginSave()!
    owner.finish(oldTicket, { version: 5 })
    expect(owner.snapshot().saving).toBe(true)
    expect(owner.snapshot().baseline?.version).toBe(10)
    expect(owner.owns(currentTicket)).toBe(true)
  })
  it('invalidates requests and save ownership on close', () => {
    const owner = workspace(deck)
    owner.edit(model => ({ ...model, title: 'Draft' }))
    const ticket = owner.beginSave()!
    owner.close()
    expect(owner.owns(ticket)).toBe(false)
    owner.finish(ticket, { version: 7 })
    expect(owner.snapshot().baseline?.version).toBe(4)
  })
  it('notifies subscribed consumers and stops after unsubscribe', () => {
    const owner = workspace(sheet)
    const snapshots: unknown[] = []
    const unsubscribe = owner.subscribe(() => snapshots.push(owner.snapshot()))
    owner.edit(model => withCell(model, 0, 0, 0, { ...emptyCell(), value: 5 }))
    unsubscribe()
    owner.edit(model => withCell(model, 0, 0, 0, { ...emptyCell(), value: 6 }))
    expect(snapshots).toHaveLength(1)
    expect(snapshots[0]).not.toBe(owner.snapshot())
  })
  it('reports noun-specific conflicts and ordinary load/save errors', () => {
    expect(structuredSaveError('deck', new ApiError('conflict', 409))).toContain('This deck changed somewhere else')
    expect(structuredSaveError('workbook', new ApiError('conflict', 409))).toContain('This workbook changed somewhere else')
    expect(structuredSaveError('deck', new Error('Unavailable'))).toBe('Unavailable')
    const owner = new StructuredEditorWorkspace<SheetModelJson>()
    const generation = owner.open('missing')
    owner.reject(generation, 'Missing')
    expect(owner.snapshot().loadError).toBe('Missing')
  })
})

describe('sheet model boundary cases', () => {
  it('keeps blank-ish, infinite and equals-prefixed literals distinct from valid numeric entries', () => {
    expect(parseEntry('\t')).toEqual({ formula: '', value: '\t' })
    expect(parseEntry('Infinity')).toEqual({ formula: '', value: 'Infinity' })
    expect(parseEntry('  FALSE ')).toEqual({ formula: '', value: false })
    expect(parseEntry('1e3')).toEqual({ formula: '', value: 1000 })
    expect(parseEntry(' =A1')).toEqual({ formula: '', value: ' =A1' })
  })
  it('preserves all cell styling while converting formula and literal representation', () => {
    const cell = { ...emptyCell(), value: '=A1', bold: true, fill: '#aabbcc', align: 'right', number_format: '0.00' }
    expect(asLiteral(asFormula(cell))).toEqual(cell)
  })
  it('pads cells independently while retaining merges, widths and frozen headers', () => {
    const next = withCell(sheet, 0, 0, 3, { ...emptyCell(), value: 12 })
    expect(next.sheets[0].cells[0][1]).not.toBe(next.sheets[0].cells[0][2])
    expect(next.sheets[0].column_widths).toBe(sheet.sheets[0].column_widths)
    expect(next.sheets[0].merges).toBe(sheet.sheets[0].merges)
    expect(next.sheets[0].frozen_header).toBe(true)
    expect(sheet.sheets[0].cells[0]).toHaveLength(1)
    expect(withCell(sheet, 3, 0, 0, emptyCell())).toBe(sheet)
  })
  it('labels spreadsheet boundaries without looping on invalid indexes', () => {
    expect([701, 702, 16383].map(columnLabel)).toEqual(['ZZ', 'AAA', 'XFD'])
    expect(columnLabel(Infinity)).toBe('')
    expect(columnLabel(-1)).toBe('')
  })
})

function SheetWorkspace() {
  const [draft, setDraft] = useState(sheet)
  const [selected, setSelected] = useState<{ row: number; col: number } | null>(null)
  return <><SheetCells sheet={draft.sheets[0]} selected={selected} editable reason="" onSelect={setSelected} onEdit={({ row, col }, cell) => setDraft(current => withCell(current, 0, row, col, cell))} /><output aria-label="Workbook draft">{JSON.stringify(draft)}</output></>
}
function DeckWorkspace() {
  const [draft, setDraft] = useState(deck)
  return <><SlideOutline slide={draft.slides[0]} index={0} editable reason="" onEdit={transform => setDraft(transform)} /><output aria-label="Deck draft">{JSON.stringify(draft)}</output></>
}
const readDeck = () => JSON.parse(screen.getByLabelText('Deck draft').textContent!) as DeckModelJson

describe('live sheet and deck model composition', () => {
  it('edits a padded address with formula semantics and names every visible input', () => {
    render(<SheetWorkspace />)
    expect(screen.getAllByRole('textbox')).toHaveLength(4)
    const input = screen.getByRole('textbox', { name: 'Plan!B1' })
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '=A1+3' } })
    const draft = JSON.parse(screen.getByLabelText('Workbook draft').textContent!) as SheetModelJson
    expect(draft.sheets[0].cells[0][1]).toMatchObject({ formula: '=A1+3', value: null })
    expect(screen.getByRole('textbox', { name: 'Plan!B2' })).toHaveValue('=A1*2')
  })
  it('edits notes, inherits new bullet depth, then removes only the selected bullet', () => {
    render(<DeckWorkspace />)
    fireEvent.change(screen.getByLabelText('Speaker notes for slide 1'), { target: { value: 'Updated notes' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add bullet' }))
    expect(screen.getByLabelText('Indent level of bullet 2 on slide 1')).toHaveValue('2')
    fireEvent.change(screen.getByLabelText('Bullet 2 on slide 1'), { target: { value: 'Second' } })
    fireEvent.click(screen.getByRole('button', { name: 'Remove bullet 1 on slide 1' }))
    expect(readDeck().slides[0].bullets).toEqual([{ text: 'Second', level: 2 }])
    expect(readDeck().slides[0].notes).toBe('Updated notes')
    expect(readDeck().slides[1]).toEqual(deck.slides[1])
  })
  it('releases placed geometry without changing bullets, notes or custom dimensions', () => {
    render(<DeckWorkspace />)
    fireEvent.click(screen.getByRole('button', { name: 'Use the layout’s positions' }))
    expect(readDeck().slides[0].title_box.width_in).toBe(0)
    expect(readDeck().width_in).toBe(12)
    expect(readDeck().slides[0].bullets).toEqual(deck.slides[0].bullets)
  })
})

describe('deck structure boundaries', () => {
  it('keeps new slide boxes independent and preserves existing slide identities', () => {
    const next = withAppendedSlide(deck, -1)
    const added = next.slides.at(-1)!
    expect(added.title_box).not.toBe(added.body_box)
    expect(next.slides[0]).toBe(deck.slides[0])
    expect(withoutSlide(next, 2).slides).toEqual(deck.slides)
    expect(withInheritedBoxes(deck, 99)).toBe(deck)
  })
  it('retains unknown layouts, custom dimensions and empty-title bullet labels', () => {
    expect(layoutOptions('Private layout').at(-1)?.value).toBe('Private layout')
    expect(slideSizeKey(deck)).toBe('custom')
    expect(withSlideSize(deck, 'custom')).toBe(deck)
    expect(slideLabel({ ...emptySlide(), bullets: [{ text: ' ', level: 0 }, { text: 'Actual title', level: 1 }] }, 1)).toBe('Actual title')
    expect(withAppendedBullet(deck, 99)).toBe(deck)
  })
})

describe('actual infographic engine adapter', () => {
  it('shares one module load and removes remote font locations from the real registry', async () => {
    const first = loadInfographicEngine()
    expect(loadInfographicEngine()).toBe(first)
    const Constructor = await first
    const module = await import('@antv/infographic')
    expect(Constructor).toBe(module.Infographic)
    for (const family of INFOGRAPHIC_FONT_FAMILIES) {
      expect(module.getFont(family)).toMatchObject({ baseUrl: '', fontWeight: {} })
    }
  })
  it('retains its host through parser errors, streamed replacement and theme recreation', async () => {
    const view = render(<InfographicView title="Diagram" content="not-an-infographic" mode="light" />)
    await waitFor(() => expect(view.container.querySelector('[data-infographic-status]')).toHaveAttribute('data-infographic-status', 'failed'))
    const host = view.container.querySelector('[data-infographic-status] > div')
    expect(host).toBeInTheDocument()
    expect(screen.getByText('not-an-infographic')).toBeInTheDocument()
    view.rerender(<InfographicView title="Diagram" content="still-incomplete-source" mode="dark" />)
    await waitFor(() => expect(view.container.querySelector('[data-infographic-status]')).toHaveAttribute('data-infographic-status', 'failed'))
    expect(view.container.querySelector('[data-infographic-status] > div')).toBe(host)
    expect(screen.getByText('still-incomplete-source')).toBeInTheDocument()
  })
})
