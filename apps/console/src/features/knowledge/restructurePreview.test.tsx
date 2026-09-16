
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReadingView } from './ReadingView'
import { RestructureControl } from './RestructureControl'
import { api, type KnowledgeItem, type KnowledgeRestructurePlan } from '../../shared/data/api'

const BODY = 'opening words\n\n## Eviction policy\nleast recently used\n'

const ITEM: KnowledgeItem = {
  id: 'item-1',
  title: 'Caching notes',
  content: BODY,
  item_type: 'note',
  tags: ['infra'],
  word_count: 8,
}

function plan(over: Partial<KnowledgeRestructurePlan> = {}): KnowledgeRestructurePlan {
  return {
    verb: 'split',
    item_id: ITEM.id,
    summary: 'Split into 2 items: “Caching notes” keeps 2 words, and 4 words move to “Eviction policy”',
    token: 'tok-abc',
    affected: [ITEM.id],
    breaks: [],
    relink_offered: false,
    detail: {},
    ...over,
  }
}

let sections: MockInstance<typeof api.knowledgeItemSections>
let duplicates: MockInstance<typeof api.knowledgeDuplicates>
let preview: MockInstance<typeof api.knowledgeRestructurePreview>
let apply: MockInstance<typeof api.knowledgeRestructureApply>
let undo: MockInstance<typeof api.knowledgeRestructureUndo>

beforeEach(() => {
  sections = vi.spyOn(api, 'knowledgeItemSections').mockResolvedValue({
    sections: [{ offset: 15, line: 3, title: 'Eviction policy', level: 2, chars: 40 }],
    length: BODY.length,
  })
  duplicates = vi.spyOn(api, 'knowledgeDuplicates').mockResolvedValue([])
  preview = vi.spyOn(api, 'knowledgeRestructurePreview').mockResolvedValue({
    confirmed: false, token: 'tok-abc', plan: plan(),
  })
  apply = vi.spyOn(api, 'knowledgeRestructureApply').mockResolvedValue({
    ok: true, confirmed: true, kept: ITEM.id, created: ['child-1'],
    undo_token: 'tok-abc', summary: 'Split into 2 items', idempotent: false,
    annotations_moved: 1,
  })
  undo = vi.spyOn(api, 'knowledgeRestructureUndo').mockResolvedValue({
    ok: true, verb: 'split', item_id: ITEM.id, summary: 'Split into 2 items',
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

async function openPanel() {
  render(<RestructureControl item={ITEM} onDone={() => {}} />)
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /restructure/i }))
  })
  await waitFor(() => expect(sections).toHaveBeenCalled())
}

async function previewSplit() {
  await act(async () => {
    fireEvent.click(screen.getByRole('checkbox', { name: /split off the section .*eviction policy/i }))
  })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /preview the change/i }))
  })
  await waitFor(() => expect(preview).toHaveBeenCalled())
}

describe('the verb is reachable from the reading surface', () => {
  it('puts a named restructure control in the reading rail', () => {
    render(<ReadingView item={ITEM} annotations={[]} onAnnotationsChanged={() => {}} />)

    expect(screen.getByRole('button', { name: /restructure/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /find/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /highlight selection/i })).toBeInTheDocument()
  })

  it('does not fetch anything until the reader opens it', () => {
    render(<ReadingView item={ITEM} annotations={[]} onAnnotationsChanged={() => {}} />)

    expect(sections).not.toHaveBeenCalled()
    expect(duplicates).not.toHaveBeenCalled()
  })
})

describe('the preview cannot be skipped', () => {
  it('previews without applying, and shows the server-composed summary', async () => {
    await openPanel()
    await previewSplit()

    expect(preview).toHaveBeenCalledWith(ITEM.id, 'split', { offsets: [15] })
    expect(apply).not.toHaveBeenCalled()
    expect(screen.getByText(/4 words move to .*Eviction policy/)).toBeInTheDocument()
  })

  it('confirms with the token the preview returned, not a bare confirm flag', async () => {
    await openPanel()
    await previewSplit()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /split this item/i }))
    })

    await waitFor(() => expect(apply).toHaveBeenCalled())
    expect(apply).toHaveBeenCalledWith(ITEM.id, 'split', { offsets: [15] }, 'tok-abc', true)
  })

  it('offers no preview until the reader has chosen a section, and says why', async () => {
    await openPanel()

    const button = screen.getByRole('button', { name: /preview the change/i })
    expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(button).toHaveAccessibleDescription(/choose at least one section/i)
  })
})

describe('the break list is a decision, not a notice', () => {
  it("renders each break's own sentence and the relink offer", async () => {
    preview.mockResolvedValue({
      confirmed: false,
      token: 'tok-abc',
      plan: plan({
        relink_offered: true,
        breaks: [
          {
            kind: 'citation_chunk',
            message: '2 citations name a specific passage of this item by chunk number',
            relinkable: true,
            refs: ['citer-1'],
          },
          {
            kind: 'annotation',
            message: '1 highlight sits across the cut',
            relinkable: false,
            refs: ['ann-1'],
          },
        ],
      }),
    })
    await openPanel()
    await previewSplit()

    expect(screen.getByText(/2 citations name a specific passage/)).toBeInTheDocument()
    expect(screen.getByText(/1 highlight sits across the cut/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /relink the references/i })).toBeChecked()
  })

  it('sends relink false when the reader unticks the offer', async () => {
    preview.mockResolvedValue({
      confirmed: false,
      token: 'tok-abc',
      plan: plan({
        relink_offered: true,
        breaks: [{ kind: 'wikilink', message: '3 links would stop resolving', relinkable: true, refs: [] }],
      }),
    })
    await openPanel()
    await previewSplit()

    await act(async () => {
      fireEvent.click(screen.getByRole('checkbox', { name: /relink the references/i }))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /split this item/i }))
    })

    await waitFor(() => expect(apply).toHaveBeenCalled())
    expect(apply).toHaveBeenCalledWith(ITEM.id, 'split', { offsets: [15] }, 'tok-abc', false)
  })

  it('says plainly when nothing would break', async () => {
    await openPanel()
    await previewSplit()

    expect(screen.getByText(/nothing points at this item/i)).toBeInTheDocument()
  })
})

describe('the undo is offered where the change happened', () => {
  it('shows an undo after applying and calls it with the returned token', async () => {
    await openPanel()
    await previewSplit()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /split this item/i }))
    })
    await waitFor(() => expect(apply).toHaveBeenCalled())

    const button = screen.getByRole('button', { name: /undo this restructure/i })
    await act(async () => { fireEvent.click(button) })

    await waitFor(() => expect(undo).toHaveBeenCalledWith('tok-abc'))
  })

  it("reports what the verb actually did rather than a bare done", async () => {
    await openPanel()
    await previewSplit()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /split this item/i }))
    })

    await waitFor(() => expect(screen.getByText('1 new item created and linked back')).toBeInTheDocument())
    expect(screen.getByText('1 highlight followed their text')).toBeInTheDocument()
    expect(document.body.textContent, 'no hedged noun survives on this panel').not.toMatch(/\((s|es)\)/)
  })

  it('and the same lines read PLURAL when the verb touched more than one', async () => {
    apply.mockResolvedValue({
      ok: true, confirmed: true, kept: ITEM.id, created: ['child-1', 'child-2'],
      undo_token: 'tok-abc', summary: 'Split into 3 items', idempotent: false,
      annotations_moved: 3,
    })
    await openPanel()
    await previewSplit()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /split this item/i }))
    })

    await waitFor(() => expect(screen.getByText('2 new items created and linked back')).toBeInTheDocument())
    expect(screen.getByText('3 highlights followed their text')).toBeInTheDocument()
  })
})

describe('a verb whose precondition is missing explains itself', () => {
  it('disables extract until the reader has selected a passage', async () => {
    await openPanel()

    await act(async () => {
      fireEvent.change(screen.getByRole('combobox', { name: /restructure verb/i }), {
        target: { value: 'extract' },
      })
    })

    expect(screen.getByText(/select a passage in the article first/i)).toBeInTheDocument()
  })

  it('says there is nothing to merge rather than offering an empty picker', async () => {
    await openPanel()

    await act(async () => {
      fireEvent.change(screen.getByRole('combobox', { name: /restructure verb/i }), {
        target: { value: 'merge' },
      })
    })

    expect(screen.getByText(/no near-duplicates were found/i)).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: /item to fold into this one/i })).toBeNull()
  })

  it('says a headingless item cannot be split instead of offering an empty list', async () => {
    sections.mockResolvedValue({ sections: [], length: 20 })
    await openPanel()

    expect(screen.getByText(/no headings, so there is no section boundary/i)).toBeInTheDocument()
  })
})
