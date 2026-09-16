import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { DeckModelJson, DocumentLossReport } from '../../data/api'

const confirmSpy = vi.fn<(opts: unknown) => Promise<boolean>>()
vi.mock('../dialog', () => ({ confirm: (opts: unknown) => confirmSpy(opts) }))

const artifactDeckModel = vi.fn()
const saveArtifactDeckModel = vi.fn()
vi.mock('../../data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      artifactDeckModel: (s: string) => artifactDeckModel(s),
      saveArtifactDeckModel: (...a: unknown[]) => saveArtifactDeckModel(...a),
    },
  }
})

const { SlideDeck } = await import('./SlideDeck')
const { ApiError } = await import('../../data/api')
const { inheritedBox } = await import('./deckModelEdit')

const MODEL: DeckModelJson = {
  title: 'Quarterly Review',
  width_in: 0,
  height_in: 0,
  slides: [
    {
      title: 'Pipeline',
      bullets: [{ text: 'Enterprise', level: 0 }, { text: 'Two renewals at risk', level: 1 }],
      notes: 'lead with the renewals',
      artifact_slug: '',
      layout: 'Title and Content',
      title_box: inheritedBox(),
      body_box: inheritedBox(),
    },
  ],
}

const LOSSLESS: DocumentLossReport = { lossless: true, kinds: [], summary: 'no losses', items: [] }
const LOSSY: DocumentLossReport = {
  lossless: false,
  kinds: ['slide_shape'],
  summary: 'slide_shape×1',
  items: [{ kind: 'slide_shape', detail: 'TextBox 3 (TEXT_BOX (17)) is not carried by the model', where: 'slide 1', block_index: -1, paragraph_ordinal: -1 }],
}

const clone = (m: DeckModelJson): DeckModelJson => JSON.parse(JSON.stringify(m))

function load(loss: DocumentLossReport = LOSSLESS, model: DeckModelJson = MODEL) {
  artifactDeckModel.mockResolvedValue({ slug: 'review', kind: 'pptx', version: 4, mime: 'x', model: clone(model), loss })
}

beforeEach(() => {
  vi.clearAllMocks()
  confirmSpy.mockResolvedValue(true)
  saveArtifactDeckModel.mockResolvedValue({ slug: 'review', version: 5, mime: 'x' })
})


describe('the pptx content type mounts the deck editor', () => {
  it('registers SlideDeck for pptx, and not for the other two office types', async () => {
    const { registerBuiltinContentTypes } = await import('./registerBuiltins')
    const { getContentType } = await import('./contentTypes')
    const { setDocumentEditing } = await import('./documentEditing')
    const { DocumentEditor } = await import('./DocumentEditor')
    const { SheetGrid } = await import('./SheetGrid')

    registerBuiltinContentTypes()
    setDocumentEditing(true)

    expect(getContentType('pptx')?.edit?.render).toBe(SlideDeck)
    expect(getContentType('docx')?.edit?.render).toBe(DocumentEditor)
    expect(getContentType('xlsx')?.edit?.render).toBe(SheetGrid)
  })
})


describe('the lossy-edit gate', () => {
  it('disables every field and says why until it is acknowledged', async () => {
    load(LOSSY)
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    await screen.findByRole('alert')

    const bullet = await screen.findByLabelText('Bullet 1 on slide 1')
    expect(bullet).toBeDisabled()
    expect(bullet).toHaveAttribute('title', expect.stringContaining('edit anyway'))
    expect(await screen.findByLabelText('Deck title')).toBeDisabled()
    expect(await screen.findByLabelText('Indent level of bullet 1 on slide 1')).toBeDisabled()
    expect(await screen.findByLabelText('Speaker notes for slide 1')).toBeDisabled()
    expect(screen.getByText(/TextBox 3/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /edit anyway/i }))
    expect(await screen.findByLabelText('Bullet 1 on slide 1')).not.toBeDisabled()
  })

  it('needs no acknowledgement for a deck the model can hold fully', async () => {
    load(LOSSLESS)
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    expect(await screen.findByLabelText('Bullet 1 on slide 1')).not.toBeDisabled()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('blocks a read-only version with its own reason', async () => {
    load(LOSSLESS)
    render(<SlideDeck slug="review" title="Review" mode="light" readOnly />)
    const bullet = await screen.findByLabelText('Bullet 1 on slide 1')
    expect(bullet).toBeDisabled()
    expect(bullet).toHaveAttribute('title', expect.stringContaining('read-only'))
  })
})


describe('the save payload', () => {
  it('sends the edited depth and the loaded version', async () => {
    load(LOSSLESS)
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    const level = await screen.findByLabelText('Indent level of bullet 2 on slide 1')
    expect((level as HTMLSelectElement).value).toBe('1')
    await userEvent.selectOptions(level, '3')

    await userEvent.type(await screen.findByLabelText('Bullet 1 on slide 1'), ' pipeline')
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(saveArtifactDeckModel).toHaveBeenCalledTimes(1))
    const [slug, version, model] = saveArtifactDeckModel.mock.calls[0] as [string, number, DeckModelJson]
    expect(slug).toBe('review')
    expect(version).toBe(4)
    expect(model.slides[0].bullets).toEqual([
      { text: 'Enterprise pipeline', level: 0 },
      { text: 'Two renewals at risk', level: 3 },
    ])
    expect(model.slides[0].notes).toBe('lead with the renewals')
    expect(model.title).toBe('Quarterly Review')
  })

  it('confirms before re-rendering a lossy deck, and does not save when declined', async () => {
    load(LOSSY)
    confirmSpy.mockResolvedValue(false)
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    await userEvent.click(await screen.findByRole('button', { name: /edit anyway/i }))
    await userEvent.type(await screen.findByLabelText('Bullet 1 on slide 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1))
    expect(saveArtifactDeckModel).not.toHaveBeenCalled()
  })

  it('explains a 409 as a collision rather than losing the edits', async () => {
    load(LOSSLESS)
    saveArtifactDeckModel.mockRejectedValue(new ApiError('conflict', 409))
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    await userEvent.type(await screen.findByLabelText('Bullet 1 on slide 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))

    expect(await screen.findByText(/changed somewhere else/i)).toBeInTheDocument()
    expect((await screen.findByLabelText('Bullet 1 on slide 1')).getAttribute('value')).toContain('!')
  })
})


describe('a deck with no slides', () => {
  it('says so and offers to add one rather than rendering an empty frame', async () => {
    load(LOSSLESS, { title: '', slides: [], width_in: 0, height_in: 0 })
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    expect(await screen.findByText(/no slides/i)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /add slide/i })).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: /^add slide$/i }))
    expect(await screen.findByLabelText('Title of slide 1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add slide after this one' })).toBeInTheDocument()
  })
})

describe('a moved shape', () => {
  it('says the shape was moved, and offers the layout’s position back', async () => {
    load(LOSSLESS, {
      ...MODEL,
      slides: [{ ...MODEL.slides[0], title_box: { left_in: 1.25, top_in: 0.5, width_in: 6, height_in: 1.5 } }],
    })
    render(<SlideDeck slug="review" title="Review" mode="light" />)
    expect(await screen.findByText(/moved out of their layout/i)).toBeInTheDocument()
    expect(screen.getByText(/1.25 × 0.5 in from the top-left/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /use the layout’s positions/i }))
    await waitFor(() => expect(screen.queryByText(/moved out of their layout/i)).toBeNull())
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(saveArtifactDeckModel).toHaveBeenCalledTimes(1))
    const [, , model] = saveArtifactDeckModel.mock.calls[0] as [string, number, DeckModelJson]
    expect(model.slides[0].title_box).toEqual(inheritedBox())
  })
})
