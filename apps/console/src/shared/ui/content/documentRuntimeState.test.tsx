import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ApiError, type DocumentBlock, type DocumentLossReport, type DocumentModelJson, type DocumentRun } from '../../data/api'
import { documentEditReducer, documentIsDirty, documentSaveError, emptyDocumentState } from './documentEditorState'
import { applyMark, blockText, EMPTY_PAGE, EMPTY_STYLE, mergeRuns, pageOf, selectionHasMark, setBlockText, styleOf, withPage, withStyle } from './documentModelEdit'
import { cmToPt, pageSizeIn, previewGeometry, ptToCm, type PageSizeName } from './documentPage'
import { PageSetupControls, ParagraphLayoutControls } from './DocumentLayout'
import { documentEditingApplied, resetDocumentEditingForTests, setDocumentEditing } from './documentEditing'
import { registerBuiltinContentTypes } from './registerBuiltins'
import { getContentType } from './contentTypes'
import { DocumentEditor } from './DocumentEditor'

const run = (text: string, marks: Partial<DocumentRun> = {}): DocumentRun => ({ text, bold: false, italic: false, code: false, link: '', ...marks })
const paragraph = (runs: DocumentRun[]): DocumentBlock => ({ kind: 'paragraph', text: '', level: 1, items: [], rows: [], artifact_slug: '', runs, cells: [], style: null })
const model: DocumentModelJson = { title: 'Report', blocks: [paragraph([run('One'), run(' two', { bold: true })]), paragraph([run('Next')])], page: null }
const clean: DocumentLossReport = { lossless: true, items: [], kinds: [], summary: 'Nothing was lost.' }
const loaded = (loss = clean) => documentEditReducer(emptyDocumentState('report'), { type: 'loaded', baseline: { model, version: 4, loss } })

describe('document revision ownership', () => {
  it('starts with a clean acknowledged lossless baseline', () => {
    const state = loaded()
    expect(state.acknowledged).toBe(true)
    expect(state.model).toBe(model)
    expect(documentIsDirty(state)).toBe(false)
  })
  it('keeps lossy acknowledgement distinct from draft changes', () => {
    const state = loaded({ ...clean, lossless: false })
    expect(state.acknowledged).toBe(false)
    const acknowledged = documentEditReducer(state, { type: 'acknowledge' })
    expect(acknowledged.acknowledged).toBe(true)
    expect(documentIsDirty(acknowledged)).toBe(false)
  })
  it('rebases only the accepted save snapshot while preserving newer edits', () => {
    const first = documentEditReducer(loaded(), { type: 'edit', transform: current => withPage(current, { header_text: 'First draft' }) })
    const newer = documentEditReducer(first, { type: 'edit', transform: current => withPage(current, { footer_text: 'Typed during save' }) })
    const saved = documentEditReducer(newer, { type: 'saved', model: first.model!, version: 5 })
    expect(saved.baseline?.version).toBe(5)
    expect(saved.baseline?.model).toBe(first.model)
    expect(saved.model?.page?.footer_text).toBe('Typed during save')
    expect(documentIsDirty(saved)).toBe(true)
    const nextSave = documentEditReducer(saved, { type: 'saved', model: saved.model!, version: 6 })
    expect(documentIsDirty(nextSave)).toBe(false)
  })
  it('opening a different document clears draft, errors, acknowledgement and pending state', () => {
    const edited = documentEditReducer(loaded(), { type: 'edit', transform: current => withPage(current, { header_text: 'Draft' }) })
    const pending = documentEditReducer(edited, { type: 'saving', value: true })
    const failed = documentEditReducer(pending, { type: 'save-error', message: 'Conflict' })
    expect(documentEditReducer(failed, { type: 'open', slug: 'other' })).toEqual(emptyDocumentState('other'))
  })
  it('keeps the draft after a save error and can dismiss just the error', () => {
    const edited = documentEditReducer(loaded(), { type: 'edit', transform: current => withStyle(current, 0, { align: 'center' }) })
    const failed = documentEditReducer(edited, { type: 'save-error', message: 'Denied' })
    expect(failed.model).toBe(edited.model)
    expect(documentIsDirty(failed)).toBe(true)
    expect(documentEditReducer(failed, { type: 'save-error', message: '' }).model).toBe(edited.model)
  })
  it('does not invent a model when an edit or save arrives before loading', () => {
    const empty = emptyDocumentState('report')
    expect(documentEditReducer(empty, { type: 'edit', transform: current => withPage(current, { size: 'a4' }) })).toBe(empty)
    expect(documentEditReducer(empty, { type: 'saved', model, version: 5 })).toBe(empty)
    expect(documentEditReducer(empty, { type: 'load-failed', message: 'Missing' }).loadError).toBe('Missing')
  })
  it('distinguishes version conflicts from other save failures', () => {
    expect(documentSaveError(new ApiError('stale', 409))).toContain('Your edits are still here')
    expect(documentSaveError(new ApiError('Permission denied', 403))).toBe('Permission denied')
    expect(documentSaveError('offline')).toBe('offline')
  })
})

describe('character range ownership', () => {
  it('preserves links and independent marks across overlapping selections', () => {
    const block = paragraph([run('first', { link: 'https://example.test/a' }), run('second', { italic: true, link: 'https://example.test/b' })])
    const formatted = applyMark(block, 3, 8, 'code', true)
    expect(formatted.runs.map(item => [item.text, item.code, item.link])).toEqual([
      ['fir', false, 'https://example.test/a'], ['st', true, 'https://example.test/a'],
      ['sec', true, 'https://example.test/b'], ['ond', false, 'https://example.test/b'],
    ])
    expect(selectionHasMark(formatted, 3, 8, 'code')).toBe(true)
    expect(selectionHasMark(formatted, 3, 9, 'code')).toBe(false)
    expect(block.runs).toHaveLength(2)
  })
  it('uses textarea UTF-16 ranges without splitting a selected emoji', () => {
    const formatted = applyMark(paragraph([run('A😀B')]), 1, 3, 'bold', true)
    expect(formatted.runs.map(item => [item.text, item.bold])).toEqual([['A', false], ['😀', true], ['B', false]])
    expect(blockText(formatted)).toBe('A😀B')
  })
  it('attributes insertion at a run boundary to the preceding run', () => {
    const block = paragraph([run('one', { bold: true }), run('two', { italic: true })])
    expect(setBlockText(block, 'one!two').runs).toEqual([run('one!', { bold: true }), run('two', { italic: true })])
  })
  it('removes an entire run without merging distinct neighboring links', () => {
    const block = paragraph([run('a', { link: '/a' }), run('X', { bold: true }), run('b', { link: '/b' })])
    expect(setBlockText(block, 'ab').runs).toEqual([run('a', { link: '/a' }), run('b', { link: '/b' })])
    expect(mergeRuns([run('a', { link: '/a' }), run('b', { link: '/b' })])).toHaveLength(2)
    expect(setBlockText(block, '').runs).toEqual([])
  })
  it('keeps layout patching immutable and leaves other blocks untouched', () => {
    const page = withPage(model, { size: 'a4' })
    const style = withStyle(page, 0, { first_line_indent_pt: -18 })
    expect(page.page).not.toBe(EMPTY_PAGE)
    expect(style.blocks[0].style).not.toBe(EMPTY_STYLE)
    expect(style.blocks[1]).toBe(model.blocks[1])
    expect(style.blocks[0].runs).toBe(model.blocks[0].runs)
    expect(model.page).toBeNull()
    expect(model.blocks[0].style).toBeNull()
    expect(withStyle(model, 99, { align: 'left' })).toBe(model)
  })
})

describe('document geometry', () => {
  it('rotates each supported paper size without mutating its dimensions', () => {
    for (const size of ['letter', 'a4', 'legal', 'tabloid'] as const) {
      const portrait = pageSizeIn(size, 'portrait')!
      const landscape = pageSizeIn(size, 'landscape')!
      expect(landscape).toEqual({ width: portrait.height, height: portrait.width })
      expect(pageSizeIn(size, '')).toEqual(portrait)
    }
    expect(pageSizeIn('', '')).toBeNull()
    expect(pageSizeIn('unknown' as PageSizeName, '')).toBeNull()
  })
  it('clamps impossible preview margins and preserves point-centimetre conversion', () => {
    const geometry = previewGeometry('letter', 'portrait', { top: -72, bottom: 7200, left: 72, right: 0 })!
    expect(geometry.inset.top).toBe(0)
    expect(geometry.inset.bottom).toBe(45)
    expect(geometry.inset.left).toBeCloseTo(100 / 8.5)
    expect(ptToCm(cmToPt(-1.25))).toBe(-1.25)
    expect(cmToPt(2.54)).toBe(72)
  })
})

function LayoutWorkspace() {
  const [draft, setDraft] = useState(model)
  return <>
    <PageSetupControls page={pageOf(draft)} readOnly={false} disabledReason="" onChange={patch => setDraft(current => withPage(current, patch))} />
    <ParagraphLayoutControls block={draft.blocks[0]} style={styleOf(draft.blocks[0])} readOnly={false} disabledReason="" onChange={patch => setDraft(current => withStyle(current, 0, patch))} />
    <output aria-label="Document draft">{JSON.stringify(draft)}</output>
  </>
}
const readDraft = () => JSON.parse(screen.getByLabelText('Document draft').textContent!) as DocumentModelJson

describe('live layout control composition', () => {
  it('writes headers, footer numbering and converted margins into the actual model', () => {
    render(<LayoutWorkspace />)
    fireEvent.change(screen.getByLabelText('Header text'), { target: { value: 'Quarterly report' } })
    fireEvent.click(screen.getByRole('switch', { name: 'Number the pages' }))
    const margin = screen.getByLabelText('top margin in centimetres')
    fireEvent.change(margin, { target: { value: '2.5' } })
    fireEvent.blur(margin)
    const draft = readDraft()
    expect(draft.page?.header_text).toBe('Quarterly report')
    expect(draft.page?.page_numbers).toBe(true)
    expect(draft.page?.margin_top_pt).toBeCloseTo(cmToPt(2.5))
    expect(draft.blocks).toEqual(model.blocks)
  })
  it('accepts a hanging indent and paragraph keep-with-next without altering page setup', () => {
    render(<LayoutWorkspace />)
    const indent = screen.getByLabelText('First line indent, in points; negative hangs the first line')
    expect(indent).not.toHaveAttribute('min')
    fireEvent.change(indent, { target: { value: '-24' } })
    fireEvent.blur(indent)
    fireEvent.click(screen.getByRole('switch', { name: 'Keep with next paragraph' }))
    expect(readDraft().blocks[0].style).toMatchObject({ first_line_indent_pt: -24, keep_with_next: true })
    expect(readDraft().page).toBeNull()
  })
  it('exposes the supplied read-only reason while retaining the geometry preview', () => {
    render(<PageSetupControls page={{ ...EMPTY_PAGE, size: 'legal' }} readOnly disabledReason="Archived version" onChange={() => { throw new Error('Read-only controls must never dispatch edits') }} />)
    expect(screen.getByText('Archived version')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Approximate Legal portrait/ })).toBeInTheDocument()
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.queryByRole('spinbutton')).toBeNull()
  })
})

describe('editor capability registration', () => {
  afterEach(resetDocumentEditingForTests)
  it('toggles office editor capabilities without replacing their preview or export owners', () => {
    registerBuiltinContentTypes()
    resetDocumentEditingForTests()
    const before = getContentType('docx')!
    setDocumentEditing(true)
    const enabled = getContentType('docx')!
    expect(documentEditingApplied()).toBe(true)
    expect(enabled.edit?.render).toBe(DocumentEditor)
    expect(enabled.preview).toBe(before.preview)
    expect(enabled.exports).toBe(before.exports)
    setDocumentEditing(true)
    expect(getContentType('docx')).toBe(enabled)
    setDocumentEditing(false)
    expect(getContentType('docx')?.edit).toBeUndefined()
    expect(documentEditingApplied()).toBe(false)
  })
})
