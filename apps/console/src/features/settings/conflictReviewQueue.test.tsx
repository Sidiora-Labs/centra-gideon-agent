import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DurabilityPanel } from './DurabilityPanel'
import { DialogHost } from '../../shared/ui/dialog/DialogHost'
import { closeDialog, subscribeDialogs } from '../../shared/ui/dialog/dialogStore'
import { invalidateKeys } from '../../shared/data/data'
import { api, type DurabilityConflict, type DurabilityConflicts } from '../../shared/data/api'


function conflict(over: Partial<DurabilityConflict> = {}): DurabilityConflict {
  return {
    id: 'c0ffee1234567890',
    entry_id: 'tasks',
    entity_id: 'task-42',
    domain: 'work',
    surface: 'durability',
    ancestor_sha: 'aaa',
    local_sha: 'bbb',
    remote_sha: 'ccc',
    local_row: { id: 'task-42', data: { id: 'task-42', title: 'Ship the export' } },
    remote_row: { id: 'task-42', data: { id: 'task-42', title: 'Ship the exporter' } },
    detected_at: '2026-08-17T09:00:00+00:00',
    status: 'needs-review',
    proposal: null,
    rationale: '',
    proposed_at: '',
    proposal_error: '',
    resolution: '',
    resolved_at: '',
    ...over,
  }
}

function queue(over: Partial<DurabilityConflicts> = {}): DurabilityConflicts {
  const conflicts = over.conflicts ?? [conflict()]
  return {
    conflicts,
    truncated: false,
    counts: {
      total: conflicts.length,
      needs_review: conflicts.filter((c) => c.status === 'needs-review').length,
      by_surface: { durability: conflicts.filter((c) => c.status === 'needs-review').length },
      selected: conflicts.length,
      ...(over.counts ?? {}),
    },
    surfaces: { memory: 'memory', knowledge: 'knowledge', durability: 'durability' },
    sync: { enabled: true, transport: 'git-sync', configured: true, ...(over.sync ?? {}) },
    ...over,
  }
}

let resolveCall: MockInstance<typeof api.resolveDurabilityConflict>

function stubPanel(conflicts: () => Promise<DurabilityConflicts>) {
  vi.spyOn(api, 'gideonConfig').mockResolvedValue({ durability: { auto_backup: true, sync_enabled: true, sync_transport: 'git-sync' } })
  vi.spyOn(api, 'durabilityStatus').mockResolvedValue({
    enabled: true,
    export: { last_run: 0, due_in_secs: 0, due: false },
    snapshot: { last_run: 0, due_in_secs: 0, due: false },
    drill: { last_run: 0, due_in_secs: 0, due: false },
    sync: { last_run: 0, due_in_secs: 0, due: false, enabled: true, transport: 'git-sync', encrypt: 'auto', encrypted: false },
  })
  vi.spyOn(api, 'durabilityArchive').mockResolvedValue({
    directory: '/tmp/snapshots', archives: [], would_prune: [],
    tiers: { daily: 14, weekly: 8, monthly: 12 },
    last_drill: { ran: false, ok: null, at: 0, detail: '', archive: '' },
  })
  vi.spyOn(api, 'settingsProviders').mockResolvedValue([
    { name: 'git-sync', displayName: 'Git sync', enabled: true, provider: { type: 'sync', hasConfigSchema: true } },
  ])
  vi.spyOn(api, 'durabilityConflicts').mockImplementation(conflicts)
  resolveCall = vi.spyOn(api, 'resolveDurabilityConflict').mockResolvedValue({
    ok: true, choice: 'take_remote', id: 'c0ffee1234567890', written: 1, removed: 0,
    conflict: conflict({ status: 'resolved', resolution: 'take_remote' }),
  })
}

function mount() {
  return render(<><DurabilityPanel /><DialogHost /></>)
}

beforeEach(() => {
  invalidateKeys('settings:durability')
})

afterEach(() => {
  let pending: { id: number }[] = []
  subscribeDialogs((list) => { pending = list })()
  for (const d of pending) closeDialog(d.id, false)
  vi.restoreAllMocks()
})

describe('a queued conflict is reviewable', () => {
  it('renders the divergence with both machines named — the vacuity floor for this file', async () => {
    stubPanel(() => Promise.resolve(queue()))
    mount()
    await waitFor(() => expect(screen.getByText('task-42')).toBeTruthy())
    expect(screen.getByText(/in tasks/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /keep this machine/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /take the other machine/i })).toBeTruthy()
    expect(screen.queryByText(/Nothing to review/i)).toBeNull()
  })

  it('shows BOTH versions verbatim on request, so the decision is not taken on trust', async () => {
    stubPanel(() => Promise.resolve(queue()))
    mount()
    await waitFor(() => expect(screen.getByText('task-42')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /compare both versions/i }))
    await waitFor(() => expect(screen.getByText(/Ship the exporter/)).toBeTruthy())
    expect(screen.getByText(/"Ship the export"/)).toBeTruthy()
  })

  it('an undrafted merge cannot be accepted, and says why', async () => {
    stubPanel(() => Promise.resolve(queue({
      conflicts: [conflict({ proposal: null, proposal_error: 'no reasoning model configured' })],
    })))
    mount()
    await waitFor(() => expect(screen.getByText('task-42')).toBeTruthy())
    const accept = screen.getByRole('button', { name: /accept the drafted merge/i })
    expect(accept.getAttribute('aria-disabled')).toBe('true')
    fireEvent.click(accept)
    expect(resolveCall).not.toHaveBeenCalled()
    expect(screen.getByText(/no reasoning model configured/)).toBeTruthy()
  })
})

describe('resolving is confirmed, and never silent', () => {
  it('asks a question that names the version and the store, as a DANGER dialog', async () => {
    stubPanel(() => Promise.resolve(queue()))
    mount()
    await waitFor(() => expect(screen.getByText('task-42')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /take the other machine/i }))
    const dialog = await screen.findByRole('alertdialog')
    const text = dialog.textContent ?? ''
    expect(text).toMatch(/task-42/)
    expect(text).toMatch(/tasks/)
    expect(text).toMatch(/other machine/i)
    expect(resolveCall).not.toHaveBeenCalled()
  })

  it('a DISMISSED confirmation writes nothing', async () => {
    stubPanel(() => Promise.resolve(queue()))
    mount()
    await waitFor(() => expect(screen.getByText('task-42')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /take the other machine/i }))
    const dialog = await screen.findByRole('alertdialog')
    const cancel = Array.from(dialog.querySelectorAll('button')).find((b) => /cancel/i.test(b.textContent ?? ''))
    expect(cancel).toBeTruthy()
    fireEvent.click(cancel!)
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(resolveCall).not.toHaveBeenCalled()
  })

  it('a CONFIRMED choice sends exactly that choice', async () => {
    stubPanel(() => Promise.resolve(queue()))
    mount()
    await waitFor(() => expect(screen.getByText('task-42')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /take the other machine/i }))
    const dialog = await screen.findByRole('alertdialog')
    const go = Array.from(dialog.querySelectorAll('button')).find((b) => /write it/i.test(b.textContent ?? ''))
    fireEvent.click(go!)
    await waitFor(() => expect(resolveCall).toHaveBeenCalledWith('c0ffee1234567890', 'take_remote'))
  })
})

describe('an empty queue and a failed read are different answers', () => {
  it('a FAILED read renders an error, not "nothing to review"', async () => {
    stubPanel(() => Promise.reject(new Error('queue unreadable')))
    mount()
    await waitFor(() => expect(screen.getByText(/could not be read/i)).toBeTruthy())
    expect(screen.getByText(/queue unreadable/)).toBeTruthy()
    expect(screen.queryByText(/Nothing to review\./i)).toBeNull()
    expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy()
  })

  it('an empty queue on an UNCONFIGURED instance says sync never ran', async () => {
    stubPanel(() => Promise.resolve(queue({
      conflicts: [],
      counts: { total: 0, needs_review: 0, by_surface: {}, selected: 0 },
      sync: { enabled: false, transport: '', configured: false },
    })))
    mount()
    await waitFor(() => expect(screen.getByText(/sync has never run/i)).toBeTruthy())
  })

  it('an empty queue on a CONFIGURED instance says nothing diverged', async () => {
    stubPanel(() => Promise.resolve(queue({
      conflicts: [],
      counts: { total: 0, needs_review: 0, by_surface: {}, selected: 0 },
    })))
    mount()
    await waitFor(() => expect(screen.getByText(/merged cleanly/i)).toBeTruthy())
    expect(screen.queryByText(/sync has never run/i)).toBeNull()
  })

  it('reports what waits on the memory and knowledge surfaces rather than implying none', async () => {
    stubPanel(() => Promise.resolve(queue({
      conflicts: [],
      counts: { total: 2, needs_review: 2, by_surface: { memory: 2 }, selected: 0 },
    })))
    mount()
    await waitFor(() => expect(screen.getByText(/2 memory conflicts are waiting/i)).toBeTruthy())
  })
})

describe('the sync configuration surface', () => {
  it('offers the installed transports and reports whether shards leave readable', async () => {
    stubPanel(() => Promise.resolve(queue()))
    mount()
    await waitFor(() => expect(screen.getByLabelText('Sync transport')).toBeTruthy())
    const select = screen.getByLabelText('Sync transport') as HTMLSelectElement
    expect(Array.from(select.options).map((o) => o.value)).toContain('git-sync')
    expect(screen.getByLabelText('Encrypt shards')).toBeTruthy()
    expect(screen.getByText(/readable by anyone with access/i)).toBeTruthy()
  })

  it('a failed transport read is not reported as "none installed"', async () => {
    stubPanel(() => Promise.resolve(queue()))
    vi.spyOn(api, 'settingsProviders').mockRejectedValue(new Error('providers offline'))
    mount()
    await waitFor(() => expect(screen.getByText(/could not be read \(providers offline\)/i)).toBeTruthy())
    expect(screen.queryByText(/No sync transport is installed/i)).toBeNull()
  })
})
