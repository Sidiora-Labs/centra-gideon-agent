import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DurabilityPanel } from './DurabilityPanel'
import { DialogHost } from '../../shared/ui/dialog/DialogHost'
import { closeDialog, subscribeDialogs } from '../../shared/ui/dialog/dialogStore'
import { invalidateKeys } from '../../shared/data/data'
import {
  api,
  type DurabilityHistoryDiffFile,
  type DurabilityHistoryEntry,
  type DurabilityHistoryPreview,
  type DurabilityHistoryTimeline,
} from '../../shared/data/api'


const SHA_OLD = 'a'.repeat(40)
const SHA_NEW = 'b'.repeat(40)
const HEAD_WHOLE = 'b'.repeat(40)
const HEAD_SUBSET = 'c'.repeat(40)

function entry(over: Partial<DurabilityHistoryEntry> = {}): DurabilityHistoryEntry {
  return {
    sha: SHA_OLD,
    short: 'aaaaaaaa',
    at: Math.floor(Date.now() / 1000) - 600,
    subject: 'Configuration: 3 files changed',
    surface: 'interactive',
    unattended: false,
    ...over,
  }
}

function timeline(entries: DurabilityHistoryEntry[]): DurabilityHistoryTimeline {
  return { root: 'config', label: 'Configuration', commits: entries.length, entries, forward_refs: [] }
}

function file(path: string): DurabilityHistoryDiffFile {
  return { path, status: 'M', bytes: 120, rendered: true, diff: `--- a/${path}\n+++ b/${path}\n-old\n+new\n` }
}

const ALL = ['config.json', 'skills/review.md', 'memory/notes.md']

function preview(over: Partial<DurabilityHistoryPreview> = {}): DurabilityHistoryPreview {
  return {
    operation: 'rollback',
    root: 'config',
    target: SHA_OLD,
    head: SHA_NEW,
    files: ALL.map(file),
    commits_rolled_away: 2,
    reversible: true,
    paths: [],
    ...over,
  }
}

let previewCall: MockInstance<typeof api.durabilityHistoryPreview>
let applyCall: MockInstance<typeof api.durabilityHistoryApply>

function stubPanel(opts: { op?: 'rollback' | 'revert'; applyError?: Error; previewError?: Error } = {}) {
  const entries = [entry({ sha: SHA_NEW }), entry()]
  vi.spyOn(api, 'gideonConfig').mockResolvedValue({
    durability: { auto_backup: true, time_travel: true },
  })
  vi.spyOn(api, 'durabilityStatus').mockResolvedValue({
    enabled: true,
    export: { last_run: 0, due_in_secs: 0, due: false },
    snapshot: { last_run: 0, due_in_secs: 0, due: false },
    drill: { last_run: 0, due_in_secs: 0, due: false },
    sync: {
      last_run: 0, due_in_secs: 0, due: false, enabled: false,
      transport: '', encrypt: 'auto', encrypted: false,
    },
  })
  vi.spyOn(api, 'durabilityArchive').mockResolvedValue({
    directory: '/tmp/snapshots', archives: [], would_prune: [],
    tiers: { daily: 14, weekly: 8, monthly: 12 },
    last_drill: { ran: false, ok: null, at: 0, detail: '', archive: '' },
  })
  vi.spyOn(api, 'settingsProviders').mockResolvedValue([])
  vi.spyOn(api, 'durabilityConflicts').mockResolvedValue({
    conflicts: [], truncated: false,
    counts: { total: 0, needs_review: 0, by_surface: {}, selected: 0 },
    surfaces: { memory: 'memory', knowledge: 'knowledge', durability: 'durability' },
    sync: { enabled: false, transport: '', configured: false },
  })
  vi.spyOn(api, 'durabilityHistory').mockResolvedValue({
    enabled: true,
    git: true,
    dir: '/tmp/home/state-history',
    roots: [
      { id: 'config', label: 'Configuration', worktree: '/tmp/home', exists: true, commits: 2, memory: false },
    ],
  })
  vi.spyOn(api, 'durabilityHistoryTimeline').mockResolvedValue(timeline(entries))
  previewCall = vi
    .spyOn(api, 'durabilityHistoryPreview')
    .mockImplementation((_root, op, _sha, paths) => {
      if (opts.previewError) return Promise.reject(opts.previewError)
      const norm = [...(paths ?? [])].sort()
      return Promise.resolve({
        confirmed: false,
        expected_head: norm.length ? HEAD_SUBSET : HEAD_WHOLE,
        preview: preview({
          operation: op,
          paths: norm,
          files: norm.length ? norm.map(file) : ALL.map(file),
          commits_rolled_away: op === 'rollback' ? 2 : 0,
        }),
      })
    })
  applyCall = vi.spyOn(api, 'durabilityHistoryApply').mockImplementation(() =>
    opts.applyError
      ? Promise.reject(opts.applyError)
      : Promise.resolve({
        ok: true, operation: opts.op ?? 'rollback', root: 'config', head: SHA_OLD,
        prior_head: SHA_NEW, reload_required: true, paths: [],
      }),
  )
}

function mount() {
  return render(<><DurabilityPanel /><DialogHost /></>)
}

async function openPreview(verb: 'rollback' | 'revert' = 'rollback') {
  mount()
  await waitFor(() => expect(screen.getAllByText('Configuration: 3 files changed').length).toBe(2))
  const label = verb === 'rollback' ? /see going back to here/i : /see undoing just this/i
  const btn = verb === 'rollback'
    ? screen.getByRole('button', { name: label })
    : screen.getAllByRole('button', { name: label })[0]
  fireEvent.click(btn)
  await waitFor(() => expect(screen.getByText('config.json')).toBeTruthy())
}

async function tick(path: string, verb: 'rollback' | 'revert' = 'rollback') {
  const before = previewCall.mock.calls.length
  fireEvent.click(screen.getByRole('checkbox', {
    name: verb === 'rollback' ? `Roll back ${path}` : `Undo ${path}`,
  }))
  await waitFor(() => expect(previewCall.mock.calls.length).toBe(before + 1))
}

async function confirmApply(verb: 'rollback' | 'revert' = 'rollback') {
  const cardVerb = verb === 'rollback' ? /^roll back$/i : /^undo it$/i
  fireEvent.click(screen.getAllByRole('button', { name: cardVerb })[0])
  const dialog = await screen.findByRole(verb === 'rollback' ? 'alertdialog' : 'dialog')
  const text = dialog.textContent ?? ''
  const go = Array.from(dialog.querySelectorAll('button'))
    .find((b) => cardVerb.test(b.textContent?.trim() ?? ''))
  fireEvent.click(go!)
  return text
}

beforeEach(() => {
  invalidateKeys('settings:durability')
  invalidateKeys('settings:history')
  invalidateKeys('settings:history:config:all')
  invalidateKeys('settings:history:config:slept')
})

afterEach(() => {
  let pending: { id: number }[] = []
  subscribeDialogs((list) => { pending = list })()
  for (const d of pending) closeDialog(d.id, false)
  vi.restoreAllMocks()
})

describe('the changed files are individually selectable', () => {
  it('every file in the preview offers a NAMED tick — not a colour, not a position', async () => {
    stubPanel()
    await openPreview()
    for (const path of ALL) {
      const box = screen.getByRole('checkbox', { name: `Roll back ${path}` })
      expect(box).toBeTruthy()
      expect((box as HTMLInputElement).checked, 'nothing is pre-ticked — the default is the whole root').toBe(false)
    }
    expect(screen.getAllByRole('checkbox', { name: /^Roll back / }).length).toBe(3)
  })

  it('a revert names its ticks with its OWN verb, so the two operations never read alike', async () => {
    stubPanel({ op: 'revert' })
    await openPreview('revert')
    expect(screen.getByRole('checkbox', { name: 'Undo config.json' })).toBeTruthy()
    expect(screen.queryByRole('checkbox', { name: 'Roll back config.json' })).toBeNull()
  })

  it('ticking one file re-previews it as a SUBSET, and applies nothing yet', async () => {
    stubPanel()
    await openPreview()
    await tick('config.json')
    expect(previewCall).toHaveBeenLastCalledWith('config', 'rollback', SHA_OLD, ['config.json'])
    expect(applyCall).not.toHaveBeenCalled()
  })
})

describe('the confirm echoes the set the PREVIEW returned', () => {
  it('sends exactly the ticked path on BOTH phases', async () => {
    stubPanel()
    await openPreview()
    await tick('skills/review.md')
    expect(previewCall).toHaveBeenLastCalledWith('config', 'rollback', SHA_OLD, ['skills/review.md'])
    await confirmApply()
    await waitFor(() => expect(applyCall).toHaveBeenCalled())
    expect(applyCall).toHaveBeenCalledWith(
      'config', 'rollback', SHA_OLD, HEAD_SUBSET, ['skills/review.md'],
    )
  })

  it('🔑 the confirm carries the SERVER\'s normalized set, not the tick order', async () => {
    stubPanel()
    await openPreview()
    await tick('skills/review.md')
    await tick('config.json')
    const previewed = (await previewCall.mock.results.at(-1)!.value).preview.paths
    expect(previewed, 'the stub normalizes, as the route does').toEqual(['config.json', 'skills/review.md'])
    await confirmApply()
    await waitFor(() => expect(applyCall).toHaveBeenCalled())
    expect(applyCall.mock.calls[0][4]).toEqual(previewed)
    expect(applyCall.mock.calls[0][4], 'NOT the order the boxes were ticked in')
      .not.toEqual(['skills/review.md', 'config.json'])
  })

  it('un-ticking back to nothing returns to whole-root, no paths', async () => {
    stubPanel()
    await openPreview()
    await tick('config.json')
    await tick('config.json')
    expect(previewCall).toHaveBeenLastCalledWith('config', 'rollback', SHA_OLD, [])
    await confirmApply()
    await waitFor(() => expect(applyCall).toHaveBeenCalled())
    expect(applyCall).toHaveBeenCalledWith('config', 'rollback', SHA_OLD, HEAD_WHOLE)
    expect(applyCall.mock.calls[0].length).toBe(4)
  })
})

describe('whole-root behaviour is preserved for a user who never ticks a box', () => {
  it('previews and applies with no path list', async () => {
    stubPanel()
    await openPreview()
    expect(previewCall).toHaveBeenCalledWith('config', 'rollback', SHA_OLD)
    expect(previewCall.mock.calls[0].length).toBe(3)
    await confirmApply()
    await waitFor(() => expect(applyCall).toHaveBeenCalled())
    expect(applyCall).toHaveBeenCalledWith('config', 'rollback', SHA_OLD, HEAD_WHOLE)
    expect(applyCall.mock.calls[0].length, 'no fifth argument on the default path').toBe(4)
  })

  it('the WIRE body carries no `paths` key for the whole root, and the subset for a subset', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    ))
    const body = () => JSON.parse((fetchSpy.mock.calls.at(-1)![1] as RequestInit).body as string)

    await api.durabilityHistoryPreview('config', 'rollback', SHA_OLD)
    expect(body()).toEqual({ sha: SHA_OLD })
    await api.durabilityHistoryPreview('config', 'rollback', SHA_OLD, [])
    expect(body(), 'an empty selection is whole-root, not a narrowed one').toEqual({ sha: SHA_OLD })
    await api.durabilityHistoryPreview('config', 'rollback', SHA_OLD, ['config.json'])
    expect(body()).toEqual({ sha: SHA_OLD, paths: ['config.json'] })

    await api.durabilityHistoryApply('config', 'rollback', SHA_OLD, HEAD_WHOLE, [])
    expect(body()).toEqual({ sha: SHA_OLD, confirm: true, expected_head: HEAD_WHOLE })
    await api.durabilityHistoryApply('config', 'revert', SHA_OLD, HEAD_SUBSET, ['memory/notes.md'])
    expect(body()).toEqual({
      sha: SHA_OLD, confirm: true, expected_head: HEAD_SUBSET, paths: ['memory/notes.md'],
    })
  })
})

describe('the confirmation tells the truth about its blast radius', () => {
  it('a whole-root rollback keeps the words it always had', async () => {
    stubPanel()
    await openPreview()
    const text = await confirmApply()
    expect(text).toMatch(/Everything in config goes back/i)
    expect(text).toMatch(/2 changes made since are set aside/i)
    expect(text).toMatch(/credentials are untouched/i)
    expect(text, 'no count is invented where the whole root is meant').not.toMatch(/of 3 files?/i)
  })

  it('a SUBSET rollback names the count, the files, and still discards later edits', async () => {
    stubPanel()
    await openPreview()
    await tick('config.json')
    await tick('memory/notes.md')
    const text = await confirmApply()
    expect(text).toMatch(/2 of 3 files/i)
    expect(text).toMatch(/Only the 2 files you picked/i)
    expect(text).toContain('config.json')
    expect(text).toContain('memory/notes.md')
    expect(text).toMatch(/Every other file in config is left exactly as it is/i)
    expect(text).toMatch(/set aside/i)
    expect(text, 'rollback must never claim later edits are kept').not.toMatch(/edited afterwards is kept/i)
  })

  it('a SUBSET revert names the count and still KEEPS later edits', async () => {
    stubPanel({ op: 'revert' })
    await openPreview('revert')
    await tick('config.json', 'revert')
    const text = await confirmApply('revert')
    expect(text).toMatch(/1 of 3 files/i)
    expect(text).toContain('config.json')
    expect(text).toMatch(/Anything edited afterwards is kept/i)
    expect(text).toMatch(/every other file in config/i)
    expect(text, 'revert must never claim later changes are set aside').not.toMatch(/set aside/i)
  })
})

describe('a refusal is visible — a restore that silently did nothing is the worst outcome', () => {
  it('renders the typed envelope\'s message on the card', async () => {
    stubPanel({ applyError: new Error('the confirmed paths do not match the previewed set') })
    await openPreview()
    await tick('config.json')
    await confirmApply()
    await waitFor(() =>
      expect(screen.getByText(/the confirmed paths do not match the previewed set/i)).toBeTruthy())
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toMatch(/Nothing was changed/i)
    expect(screen.getByRole('checkbox', { name: 'Roll back config.json' })).toBeTruthy()
  })

  it('a refused PATH names itself before anything is applied', async () => {
    stubPanel()
    await openPreview()
    previewCall.mockRejectedValueOnce(new Error("unknown path: '../../etc/passwd'"))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Roll back config.json' }))
    await waitFor(() => expect(screen.getByText(/unknown path/i)).toBeTruthy())
    expect(screen.getByRole('alert').textContent).toContain('../../etc/passwd')
    expect(applyCall).not.toHaveBeenCalled()
  })
})
