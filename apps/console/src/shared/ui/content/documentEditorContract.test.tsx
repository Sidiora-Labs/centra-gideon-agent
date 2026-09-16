import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { DocumentBlock, DocumentLossReport, DocumentModelJson, DocumentRun } from '../../data/api'


const confirmSpy = vi.fn<(opts: unknown) => Promise<boolean>>()
vi.mock('../dialog', () => ({ confirm: (opts: unknown) => confirmSpy(opts) }))

const artifactModel = vi.fn()
const saveArtifactModel = vi.fn()
vi.mock('../../data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../data/api')>()
  return {
    ...actual,
    api: { ...actual.api, artifactModel: (s: string) => artifactModel(s), saveArtifactModel: (...a: unknown[]) => saveArtifactModel(...a) },
  }
})

const { DocumentEditor } = await import('./DocumentEditor')
const { ApiError } = await import('../../data/api')

const run = (text: string, marks: Partial<DocumentRun> = {}): DocumentRun =>
  ({ text, bold: false, italic: false, code: false, link: '', ...marks })

const block = (over: Partial<DocumentBlock>): DocumentBlock => ({
  kind: 'paragraph', text: '', level: 1, items: [], rows: [],
  artifact_slug: '', runs: [], cells: [], style: null, ...over,
})

const MODEL: DocumentModelJson = {
  title: 'Fidelity',
  blocks: [
    block({ kind: 'heading', text: 'Overview', runs: [run('Overview')] }),
    block({ kind: 'paragraph', text: 'a plain word', runs: [run('a plain word')] }),
    block({ kind: 'table', rows: [['h'], ['v']] }),
  ],
  page: null,
}

const LOSSY: DocumentLossReport = {
  lossless: false,
  kinds: ['footnote'],
  summary: '2 things will not survive an edit.',
  items: [
    { kind: 'footnote', detail: 'a footnote reference', where: 'block 1, paragraph 3', block_index: 1, paragraph_ordinal: 3 },
    { kind: 'page_property', detail: 'asymmetric margins', where: 'document', block_index: -1, paragraph_ordinal: -1 },
  ],
}

const CLEAN: DocumentLossReport = { lossless: true, kinds: [], summary: 'Nothing was lost.', items: [] }

function mount(loss: DocumentLossReport, over: { readOnly?: boolean; onDirty?: (d: boolean) => void } = {}) {
  artifactModel.mockResolvedValue({ slug: 'report', kind: 'docx', version: 4, mime: 'x', model: structuredClone(MODEL), loss })
  return render(<DocumentEditor slug="report" title="Report" mode="dark" {...over} />)
}

const paragraph = () => screen.findByLabelText('Paragraph')

function select(el: HTMLTextAreaElement, from: number, to: number) {
  el.focus()
  el.setSelectionRange(from, to)
  fireEvent.mouseUp(el)
}

beforeEach(() => {
  confirmSpy.mockReset()
  confirmSpy.mockResolvedValue(true)
  artifactModel.mockReset()
  saveArtifactModel.mockReset()
  saveArtifactModel.mockResolvedValue({ slug: 'report', version: 5, mime: 'x' })
})

describe('a lossy document warns BEFORE the first edit', () => {
  it('names what will not survive, and the fields are not editable yet', async () => {
    mount(LOSSY)
    expect(await screen.findByText('2 things will not survive an edit.')).toBeInTheDocument()
    expect(screen.getByText(/a footnote reference/)).toBeInTheDocument()
    expect(await paragraph()).toBeDisabled()
    const bold = screen.getByRole('button', { name: 'Bold' })
    expect(bold).toHaveAttribute('aria-disabled', 'true')
    expect(bold).toHaveAttribute('title', expect.stringContaining('formatting notice'))
  })

  it('acknowledging it — and only that — hands over the controls', async () => {
    mount(LOSSY)
    expect(await paragraph()).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: /I understand/ }))
    expect(await paragraph()).toBeEnabled()
  })

  it('a lossless document has no gate to click through', async () => {
    mount(CLEAN)
    expect(await paragraph()).toBeEnabled()
    expect(screen.queryByRole('button', { name: /I understand/ })).not.toBeInTheDocument()
  })

  it('a read-only host stays read-only even after acknowledgement is moot', async () => {
    mount(CLEAN, { readOnly: true })
    expect(await paragraph()).toBeDisabled()
  })
})

describe('bolding a word posts a split run', () => {
  it('sends three runs with only the selected word bold', async () => {
    mount(CLEAN)
    const field = await paragraph() as HTMLTextAreaElement
    select(field, 2, 7)
    await userEvent.click(screen.getByRole('button', { name: 'Bold' }))
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))

    await waitFor(() => expect(saveArtifactModel).toHaveBeenCalledTimes(1))
    const [slug, version, model] = saveArtifactModel.mock.calls[0] as [string, number, DocumentModelJson]
    expect(slug).toBe('report')
    expect(version).toBe(4)
    expect(model.blocks[1].runs.map((r) => [r.text, r.bold])).toEqual([
      ['a ', false], ['plain', true], [' word', false],
    ])
    expect(model.blocks[2].rows).toEqual([['h'], ['v']])
  })

  it('the save button is dead until something actually changed, and says so', async () => {
    mount(CLEAN)
    await paragraph()
    const save = screen.getByRole('button', { name: /^Save/ })
    expect(save).toHaveAttribute('aria-disabled', 'true')
    expect(save).toHaveAttribute('title', expect.stringContaining('No changes to save'))
  })
})

describe('the save confirmation repeats the report', () => {
  it('asks, with the same loss items in the body, before writing', async () => {
    mount(LOSSY)
    await userEvent.click(await screen.findByRole('button', { name: /I understand/ }))
    const field = await paragraph() as HTMLTextAreaElement
    await userEvent.type(field, '!')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1))
    const opts = confirmSpy.mock.calls[0][0] as { title: string; body: React.ReactNode; danger?: boolean }
    expect(opts.danger).toBe(true)
    expect(opts.title).toContain('Report')
    render(<>{opts.body}</>)
    expect(screen.getAllByText('2 things will not survive an edit.').length).toBeGreaterThan(0)
    expect(screen.getByText(/asymmetric margins/)).toBeInTheDocument()
    expect(screen.getByText(/Version 4 is kept/)).toBeInTheDocument()
  })

  it('a DISMISSED confirmation writes nothing', async () => {
    confirmSpy.mockResolvedValue(false)
    mount(LOSSY)
    await userEvent.click(await screen.findByRole('button', { name: /I understand/ }))
    await userEvent.type(await paragraph(), '!')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))
    await waitFor(() => expect(confirmSpy).toHaveBeenCalled())
    expect(saveArtifactModel).not.toHaveBeenCalled()
  })

  it('a lossless save is not confirmed — there is nothing to confirm', async () => {
    mount(CLEAN)
    await userEvent.type(await paragraph(), '!')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))
    await waitFor(() => expect(saveArtifactModel).toHaveBeenCalled())
    expect(confirmSpy).not.toHaveBeenCalled()
  })
})

describe('the two-tab race is reported, not merged', () => {
  it('a 409 says what happened and leaves the draft saveable', async () => {
    saveArtifactModel.mockRejectedValue(new ApiError('stale', 409))
    mount(CLEAN)
    const field = await paragraph() as HTMLTextAreaElement
    await userEvent.type(field, '!')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))

    expect(await screen.findByText(/changed somewhere else/)).toBeInTheDocument()
    expect(field).toHaveValue('a plain word!')
    expect(screen.getByRole('button', { name: /^Save/ })).toBeEnabled()
  })

  it('any other failure is surfaced too, not swallowed into a silent no-op', async () => {
    saveArtifactModel.mockRejectedValue(new ApiError('in-place document editing is off', 403))
    mount(CLEAN)
    await userEvent.type(await paragraph(), '!')
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))
    expect(await screen.findByText(/editing is off/)).toBeInTheDocument()
  })
})

describe('the host learns about unsaved work', () => {
  it('reports dirty on the first edit and clean again after a successful save', async () => {
    const onDirty = vi.fn()
    mount(CLEAN, { onDirty })
    await userEvent.type(await paragraph(), '!')
    await waitFor(() => expect(onDirty).toHaveBeenCalledWith(true))
    await userEvent.click(screen.getByRole('button', { name: /^Save/ }))
    await waitFor(() => expect(onDirty).toHaveBeenLastCalledWith(false))
  })
})

describe('a failed read is not an empty document', () => {
  it('says the read failed instead of offering an editor over nothing', async () => {
    artifactModel.mockRejectedValue(new Error('nope'))
    render(<DocumentEditor slug="report" title="Report" mode="dark" />)
    expect(await screen.findByText(/Couldn’t read Report/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Paragraph')).not.toBeInTheDocument()
  })
})
