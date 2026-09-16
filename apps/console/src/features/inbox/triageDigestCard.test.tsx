import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TriageDigestCard } from './TriageDigestCard'
import type { TriageDigestView } from '../../shared/data/api'


const proactiveDigest = vi.fn()
const proactiveReply = vi.fn((_runId: string, _text: string) => Promise.resolve({ ok: true, outcome: 'acted' as const, results: [{ ordinal: '1', outcome: 'acted' as const, executed: true, recorded: true }] }))
const proactiveInstall = vi.fn((_cron?: string) => Promise.resolve({ ok: true, created: true, schedule: { id: 'system:triage:digest', name: 'Morning triage', cron: '0 8 * * *', enabled: true, created_by: 'system' } }))
const autonomyUndo = vi.fn((_id: string) => Promise.resolve({ ok: true, code: 'reversed', action_type: 'action.inbox_op', demoted: false }))

vi.mock('../../shared/data/api', () => ({
  api: {
    proactiveDigest: () => proactiveDigest(),
    proactiveReply: (runId: string, text: string) => proactiveReply(runId, text),
    proactiveInstall: (cron?: string) => proactiveInstall(cron),
    autonomyUndo: (id: string) => autonomyUndo(id),
  },
}))
const notify = vi.fn((_message: string, _tone?: string) => undefined)
vi.mock('../../app/shell/appSdk', () => ({ notify: (m: string, t?: string) => notify(m, t) }))

function view(over: Partial<TriageDigestView> = {}): TriageDigestView {
  return {
    state: 'ready',
    enabled: true,
    installed: true,
    error: '',
    run_id: 'run-abc',
    permalink: '#/workflows/runs/run-abc',
    title: 'Morning triage',
    collected: 3,
    dropped: 1,
    auto_stage_ran: true,
    auto_done: [],
    pending: [],
    machine_did: [],
    ledger_complete: true,
    ledger_rows: 2,
    ...over,
  }
}

const PENDING = {
  ordinal: '1',
  action_type: 'reply_draft',
  tier: 'medium',
  pattern_key: 'reply_draft:inbox',
  clamped: true,
  reason: 'needs_you',
  rule: '',
  answered: false,
  answer: '',
  permalink: '#/workflows/runs/run-abc',
  title: 'Review request on #412',
  source: 'inbox',
  item_permalink: '',
  materiality: 'action',
}

const AUTO_DONE = {
  ordinal: '2',
  source_id: 'gh_2',
  action_type: 'archive',
  provider: 'inbox-op',
  rule: 'policy:trivial-tier',
  reversal: 'aW5ib3gtb3A6Z2hfMg==',
  undoable: true,
  ok: true,
  error: '',
  permalink: '#/workflows/runs/run-abc',
  title: 'Dependabot bumped left-pad',
  source: 'inbox',
  item_permalink: '',
  materiality: 'none',
}

beforeEach(() => {
  proactiveDigest.mockReset()
  proactiveReply.mockClear()
  proactiveInstall.mockClear()
  autonomyUndo.mockClear()
  notify.mockClear()
  try { window.sessionStorage.clear() } catch {   }
})

describe('the digest card tells five different states apart', () => {
  it('renders the failure, not an empty digest, when the read rejects', async () => {
    proactiveDigest.mockRejectedValue(new Error('gateway said 500'))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/gateway said 500/)).toBeTruthy())
    expect(screen.queryByRole('button', { name: /^Install$/ })).toBeNull()
  })

  it("renders the server's own error verdict the same way", async () => {
    proactiveDigest.mockResolvedValue(view({ state: 'error', error: 'OSError: events.jsonl is unreadable' }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/events\.jsonl is unreadable/)).toBeTruthy())
  })

  it('offers §5.4 install with an editable schedule when nothing is installed', async () => {
    proactiveDigest.mockResolvedValue(view({ state: 'uninstalled', installed: false }))
    render(<TriageDigestCard />)
    const field = await screen.findByLabelText('Digest schedule (cron)')
    fireEvent.change(field, { target: { value: '30 7 * * 1-5' } })
    fireEvent.click(screen.getByRole('button', { name: /^Install$/ }))
    await waitFor(() => expect(proactiveInstall).toHaveBeenCalledWith('30 7 * * 1-5'))
  })

  it('says dormant-but-kept when triage is off, not "no digest yet"', async () => {
    proactiveDigest.mockResolvedValue(view({
      state: 'off', enabled: false,
      schedule: { id: 'system:triage:digest', name: 'Morning triage', cron: '0 8 * * *', enabled: false, created_by: 'system' },
    }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/are kept/)).toBeTruthy())
    expect(screen.getByText('0 8 * * *')).toBeTruthy()
    expect(screen.queryByText(/No digest has run yet/)).toBeNull()
  })

  it('says not-yet-run when it is installed and on but has never fired', async () => {
    proactiveDigest.mockResolvedValue(view({
      state: 'never_run',
      schedule: { id: 'system:triage:digest', name: 'Morning triage', cron: '0 8 * * *', enabled: true, created_by: 'system' },
    }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/No digest has run yet/)).toBeTruthy())
    expect(screen.queryByText(/switched off/)).toBeNull()
  })
})

describe('an unmeasured value is not rendered as a zero', () => {
  it('says auto-execution is OFF rather than reporting no actions', async () => {
    proactiveDigest.mockResolvedValue(view({ auto_stage_ran: false, auto_done: [] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/Auto-execution is off/)).toBeTruthy())
    expect(screen.queryByText(/ran and found nothing/)).toBeNull()
  })

  it('says the stage RAN and found nothing when it did — the distinguishing pair', async () => {
    proactiveDigest.mockResolvedValue(view({ auto_stage_ran: true, auto_done: [] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/ran and found nothing/)).toBeTruthy())
    expect(screen.queryByText(/Auto-execution is off/)).toBeNull()
  })

  it('reports an incomplete ledger rather than an empty one', async () => {
    proactiveDigest.mockResolvedValue(view({ ledger_complete: false, machine_did: [] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/not recorded/)).toBeTruthy())
    expect(screen.queryByText(/wrote no ledger rows/)).toBeNull()
  })

  it('reports a genuinely empty ledger as empty — the pair', async () => {
    proactiveDigest.mockResolvedValue(view({ ledger_complete: true, machine_did: [] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/wrote no ledger rows/)).toBeTruthy())
  })

  it('badges an unscored tier as untiered, never as the cheapest one', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [{ ...PENDING, tier: '', clamped: false }] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText('untiered')).toBeTruthy())
    expect(screen.queryByText('trivial')).toBeNull()
  })
})

describe('the auto-done section offers undo where an undo exists', () => {
  it('undoes through the platform reversal handle', async () => {
    proactiveDigest.mockResolvedValue(view({ auto_done: [AUTO_DONE] }))
    render(<TriageDigestCard />)
    const btn = await screen.findByRole('button', { name: /Undo/ })
    fireEvent.click(btn)
    await waitFor(() => expect(autonomyUndo).toHaveBeenCalledWith('aW5ib3gtb3A6Z2hfMg=='))
  })

  it('offers no Undo — and says why — when the provider recorded no reversal', async () => {
    proactiveDigest.mockResolvedValue(view({ auto_done: [{ ...AUTO_DONE, reversal: '', undoable: false }] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/no undo recorded/)).toBeTruthy())
    expect(screen.queryByRole('button', { name: /Undo/ })).toBeNull()
  })

  it('names the rule that authorised each action', async () => {
    proactiveDigest.mockResolvedValue(view({ auto_done: [AUTO_DONE] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText('policy:trivial-tier')).toBeTruthy())
  })
})

describe('one-tap yes / no / always emits the reply grammar', () => {
  it.each([
    ['Yes', '1 yes'],
    ['No', '1 no'],
    ['Always', 'always yes 1'],
    ['Never', 'always no 1'],
  ])('%s sends "%s"', async (label, text) => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    render(<TriageDigestCard />)
    fireEvent.click(await screen.findByRole('button', { name: label }))
    await waitFor(() => expect(proactiveReply).toHaveBeenCalledWith('run-abc', text))
  })

  it('shows the tier badge and flags a raised tier', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/needs a look \(raised\)/)).toBeTruthy())
  })

  it('withholds "always" when the run recorded no pattern to teach', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [{ ...PENDING, pattern_key: '' }] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/no pattern to remember/)).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Always' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Yes' })).toBeTruthy()
  })

  it('keeps an answered proposal visible and does not re-offer it', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [{ ...PENDING, answered: true, answer: 'always no' }] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/You answered/)).toBeTruthy())
    expect(screen.getByText('always no')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Yes' })).toBeNull()
  })

  it('surfaces an expired digest instead of acting on a stale ordinal', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    const expired = Object.assign(new Error('that digest expired — open the current one'), { status: 409 })
    proactiveReply.mockRejectedValueOnce(expired)
    render(<TriageDigestCard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Yes' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining('expired'), 'error'))
    expect(notify).not.toHaveBeenCalledWith(expect.stringContaining("Couldn't answer"), 'error')
  })

  it('reports a genuine failure as a failure — the discriminating pair', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    proactiveReply.mockRejectedValueOnce(Object.assign(new Error('HTTP 502'), { status: 502 }))
    render(<TriageDigestCard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Yes' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining("Couldn't answer"), 'error'))
  })

  it('surfaces an unrecorded answer, because the next tap would act again', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    proactiveReply.mockResolvedValueOnce({
      ok: true, outcome: 'acted' as const,
      results: [{ ordinal: '1', outcome: 'acted' as const, executed: true, recorded: false }],
    } as never)
    render(<TriageDigestCard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Yes' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining("wasn't recorded"), 'error'))
  })

  it('says nothing ran again when the ordinal was already answered', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    proactiveReply.mockResolvedValueOnce({
      ok: true, outcome: 'acted' as const,
      results: [{ ordinal: '1', outcome: 'already' as const, detail: 'already answered no' }],
    } as never)
    render(<TriageDigestCard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Yes' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining('Already answered'), 'info'))
  })
})

describe('an absent notification is explained, never claimed as delivered', () => {
  it('names the quiet-hours window that held the digest back', async () => {
    proactiveDigest.mockResolvedValue(view({
      handed_to_notify: true,
      quiet_hours: { known: true, enabled: true, start: '22:00', end: '08:00', mute_all: false },
    }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/Quiet hours 22:00–08:00/)).toBeTruthy())
    expect(screen.queryByText(/delivered/i)).toBeNull()
  })

  it('says mute-all when everything is muted, which is a different cause', async () => {
    proactiveDigest.mockResolvedValue(view({
      quiet_hours: { known: true, enabled: false, start: '', end: '', mute_all: true },
    }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/All notifications are muted/)).toBeTruthy())
    expect(screen.queryByText(/Quiet hours/)).toBeNull()
  })

  it('says UNKNOWN when the settings could not be read, not "quiet hours are off"', async () => {
    proactiveDigest.mockResolvedValue(view({
      quiet_hours: { known: false, enabled: false, start: '', end: '', mute_all: false },
    }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/is unknown/)).toBeTruthy())
  })

  it('stays silent when quiet hours are genuinely off — the vacuity pair', async () => {
    proactiveDigest.mockResolvedValue(view({
      quiet_hours: { known: true, enabled: false, start: '22:00', end: '08:00', mute_all: false },
    }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/What your machine did/)).toBeTruthy())
    expect(screen.queryByText(/Quiet hours/)).toBeNull()
    expect(screen.queryByText(/is unknown/)).toBeNull()
  })
})

describe('the ledger section permalinks into the run journal', () => {
  it('links every row at the run the digest came from', async () => {
    proactiveDigest.mockResolvedValue(view({
      machine_did: [{
        kind: 'skipped_triage', seq: 4, ordinal: '5', action_type: '', rule: 'dependabot',
        outcome: '', reason: 'automated dependency bump', detail: '', verb: '',
        permalink: '#/workflows/runs/run-abc',
      }],
    }))
    render(<TriageDigestCard />)
    const link = await screen.findByRole('link', { name: /Open the run journal for skipped_triage/ })
    expect(link.getAttribute('href')).toBe('#/workflows/runs/run-abc')
  })
})


const SRC = join(process.cwd(), "src")
const stripComments = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the cards are rendered BY their pages', () => {
  it('the inbox page renders the digest card', () => {
    const code = stripComments(readFileSync(join(SRC, 'features/inbox/InboxPage.tsx'), 'utf8'))
    expect(code).toContain("import { TriageDigestCard } from './TriageDigestCard'")
    expect(code, 'a card the page imports but never renders is a card no user sees').toMatch(/<TriageDigestCard\s*\/>/)
    expect(code).not.toMatch(/<TriageRulesCard\s*\/>/)
  })

  it('the inbox settings panel renders the rules manager and the triage switches', () => {
    const code = stripComments(readFileSync(join(SRC, 'features/settings/InboxSettingsPanel.tsx'), 'utf8'))
    expect(code).toContain("import { TriageRulesCard } from './TriageRulesCard'")
    expect(code).toMatch(/<TriageRulesCard\s*\/>/)
    expect(code).toContain("api.patchConfig('proactive.triage_enabled'")
    expect(code).toContain("api.patchConfig('proactive.auto_execute_enabled'")
    expect(code).toMatch(/patchConfig\('proactive\.triage_enabled', v\)[\s\S]{0,160}proactiveInstall\(\)/)
    expect(code).not.toMatch(/<TriageDigestCard\s*\/>/)
  })
})
