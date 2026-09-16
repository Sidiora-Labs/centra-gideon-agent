import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { DuplicateList } from './DuplicateList'
import { DialogHost } from '../../shared/ui/dialog/DialogHost'
import { closeDialog, subscribeDialogs } from '../../shared/ui/dialog/dialogStore'
import { api, type KnowledgeDuplicate } from '../../shared/data/api'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const KEEPER = { id: 'keep-1', title: 'Rust async book' }

function dup(over: Partial<KnowledgeDuplicate> = {}): KnowledgeDuplicate {
  return {
    id: 'loser-1',
    title: 'Rust async book (1)',
    item_type: 'note',
    created_at: '2026-08-10T09:00:00',
    word_count: 4200,
    reason: 'title 0.97 + cosine 0.99, same series date',
    ...over,
  }
}

function mount(props: Partial<Parameters<typeof DuplicateList>[0]> = {}) {
  const onMerged = vi.fn()
  const onRetry = vi.fn()
  const onOpenItem = vi.fn()
  const view = render(
    <>
      <DuplicateList item={KEEPER} duplicates={[dup()]} onRetry={onRetry}
        onOpenItem={onOpenItem} onMerged={onMerged} {...props} />
      <DialogHost />
    </>,
  )
  return { ...view, onMerged, onRetry, onOpenItem }
}

async function openConfirmation() {
  const trigger = screen.getByRole('button', { name: /merge into this item/i })
  fireEvent.click(trigger)
  return await screen.findByRole('alertdialog')
}

let merge: MockInstance<typeof api.mergeKnowledgeItems>

beforeEach(() => {
  merge = vi.spyOn(api, 'mergeKnowledgeItems').mockResolvedValue({
    ok: true, kept: KEEPER.id, merged: 'loser-1',
    moved: { collections: 2, tags: 1, mentions: 3, annotations: 0, relations: 1, citations: 2 },
  })
})

afterEach(() => {
  let pending: { id: number }[] = []
  subscribeDialogs((list) => { pending = list })()
  for (const d of pending) closeDialog(d.id, false)
  vi.restoreAllMocks()
})

describe('near-duplicate candidates are surfaced for an item', () => {
  it('lists the candidate with the scorer’s own reason for the match', () => {
    mount()
    expect(screen.getByText('Rust async book (1)')).toBeTruthy()
    expect(screen.getByText(/title 0\.97 \+ cosine 0\.99/)).toBeTruthy()
  })

  it('the merge control names the DIRECTION, not just "Merge"', () => {
    mount()
    expect(screen.getByRole('button', { name: /merge into this item/i })).toBeTruthy()
  })

  it('the candidate can be OPENED before being destroyed, under a name that says which', () => {
    const { onOpenItem } = mount()
    fireEvent.click(screen.getByRole('button', { name: 'Open “Rust async book (1)”' }))
    expect(onOpenItem).toHaveBeenCalledWith('loser-1')
    expect(merge).not.toHaveBeenCalled()
  })

  it('the title is not itself a control (no bespoke button in a primitive-adoption surface)', () => {
    mount()
    expect(screen.queryByRole('button', { name: 'Rust async book (1)' })).toBeNull()
    expect(screen.getByText('Rust async book (1)').tagName).toBe('P')
  })
})

describe('the confirmation names what the merge will do', () => {
  it('says which copy is KEPT and which is DELETED', async () => {
    mount()
    const dialog = await openConfirmation()
    const text = dialog.textContent ?? ''
    expect(text, 'the survivor must be identified as kept').toMatch(/item you have open is kept/i)
    expect(text, 'the deletion must be stated').toMatch(/duplicate is then deleted/i)
    expect(text, 'and the loser named').toContain('Rust async book (1)')
  })

  it('🔑 stays unambiguous when BOTH copies carry the SAME title', async () => {
    mount({
      item: { id: 'keep-1', title: 'Rust async book notes' },
      duplicates: [dup({ title: 'Rust async book notes', word_count: 31 })],
    })
    const text = (await openConfirmation()).textContent ?? ''
    expect(text, 'the survivor is named by position, which two identical titles cannot be')
      .toMatch(/item you have open is kept/i)
    expect(text, 'and the loser carries distinguishing metadata').toMatch(/31 words/)
    expect(text).toMatch(/added /)
  })

  it('says the curation MOVES and that the act is irreversible', async () => {
    mount()
    const text = (await openConfirmation()).textContent ?? ''
    expect(text).toMatch(/collections, tags, entity mentions and highlights/i)
    expect(text).toMatch(/cannot be undone/i)
  })

  it('says how to get the OPPOSITE direction instead of leaving it to be guessed', async () => {
    mount()
    const text = (await openConfirmation()).textContent ?? ''
    expect(text).toMatch(/to keep that copy instead, open it and merge from there/i)
  })

  it('is raised as an ALERT dialog, and its button names the destruction', async () => {
    mount()
    const dialog = await openConfirmation()
    expect(dialog.getAttribute('role')).toBe('alertdialog')
    expect(dialog.textContent).toMatch(/Merge and delete/)
  })
})

describe('the merge only happens on confirmation, and in the right direction', () => {
  it('cancelling fires NO request', async () => {
    const { onMerged } = mount()
    const dialog = await openConfirmation()
    fireEvent.click(Array.from(dialog.querySelectorAll('button'))
      .find((b) => /cancel/i.test(b.textContent ?? '')) as HTMLButtonElement)
    let open: { id: number }[] = []
    subscribeDialogs((list) => { open = list })()
    expect(open).toHaveLength(0)
    expect(merge).not.toHaveBeenCalled()
    expect(onMerged).not.toHaveBeenCalled()
  })

  it('confirming merges the CANDIDATE into the item being viewed — survivor first', async () => {
    const { onMerged } = mount()
    const dialog = await openConfirmation()
    fireEvent.click(Array.from(dialog.querySelectorAll('button'))
      .find((b) => /merge and delete/i.test(b.textContent ?? '')) as HTMLButtonElement)
    await waitFor(() => expect(merge).toHaveBeenCalledWith(KEEPER.id, 'loser-1'))
    await waitFor(() => expect(onMerged).toHaveBeenCalled())
  })

  it('a failed merge tells the host nothing landed', async () => {
    merge.mockRejectedValue(new Error('database is locked'))
    const { onMerged } = mount()
    const dialog = await openConfirmation()
    fireEvent.click(Array.from(dialog.querySelectorAll('button'))
      .find((b) => /merge and delete/i.test(b.textContent ?? '')) as HTMLButtonElement)
    await waitFor(() => expect(merge).toHaveBeenCalled())
    expect(onMerged).not.toHaveBeenCalled()
  })
})

describe('a failed lookup is not an empty one', () => {
  it('renders the failure and a retry, and never claims there are no duplicates', () => {
    const { onRetry } = mount({ duplicates: [], error: new Error('database is locked') })
    expect(screen.getByRole('alert').textContent).toMatch(/database is locked/)
    expect(screen.queryByText(/no duplicates/i)).toBeNull()
    expect(screen.getByText(/may still have duplicates/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(onRetry).toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /merge into this item/i })).toBeNull()
  })

  it('the failure ANNOUNCES — a silent error is one a screen-reader user never learns of', () => {
    mount({ duplicates: [], error: new Error('database is locked') })
    expect(screen.getByRole('alert')).toBeTruthy()
  })

  it('the fetcher does not substitute an empty list for a rejection', () => {
    const src = read('shared/data/api.ts')
    const helper = src.slice(src.indexOf('knowledgeDuplicates:'))
      .slice(0, src.slice(src.indexOf('knowledgeDuplicates:')).indexOf('mergeKnowledgeItems:'))
    expect(helper, 'the helper must exist to be checked').toContain('/duplicates')
    expect(helper, 'the rejection must reach the caller').not.toMatch(/\.catch\(/)
  })

  it('the page STORES the rejection instead of dropping it, and mounts the section for it', () => {
    const page = read('features/knowledge/KnowledgeDetailPage.tsx')
    expect(page).toMatch(/api\.knowledgeDuplicates\(id\)/)
    expect(page, 'the rejection is stored, not swallowed')
      .toMatch(/\.catch\(\(e\) => \{ if \(alive\) \{ setDuplicates\(\[\]\); setDuplicatesErr\(e\) \} \}\)/)
    expect(page, 'and a failed lookup must mount the section')
      .toMatch(/const showDuplicates = duplicates\.length > 0 \|\| !!duplicatesError/)
  })

  it('a merge invalidates the item AND the candidate list, not just one of them', () => {
    const page = read('features/knowledge/KnowledgeDetailPage.tsx')
    const after = page.slice(page.indexOf('const afterMerge'))
    expect(after.slice(0, 400)).toMatch(/setReloadKey/)
    expect(after.slice(0, 400)).toMatch(/reloadDuplicates\(\)/)
  })
})
