import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { refinePillLabel, TRIGGER_LABEL, SkillProposals } from './SkillProposals'
import { diffLineColor, UnifiedDiff } from '../../shared/ui/UnifiedDiff'
import { api } from '../../shared/data/api'
import type { SkillProposal, SkillProposalDetail } from '../../shared/data/api'


const proposal = (over: Partial<SkillProposal> = {}): SkillProposal => ({
  id: 'release-flow-abc123',
  slug: 'release-flow',
  description: 'Refined after you corrected this turn',
  triggers: '',
  kind: 'refine',
  refine_target: 'release-flow',
  trigger: 'correction',
  session_key: 'sess:1',
  created_at: '2026-08-25T12:00:00+00:00',
  status: 'pending',
  procedure_preview: 'When this skill applies, honor the correction…',
  ...over,
})

const DIFF = [
  '--- release-flow/SKILL.md',
  '+++ release-flow/SKILL.md',
  '@@ -4,3 +4,9 @@',
  ' Run `pip install`.',
  '-old line to drop',
  '+',
  '+## Refinement v2 (2026-08-25, from a correction)',
  '+',
  '+> No, use uv instead of pip.',
].join('\n')

const detail = (over: Partial<SkillProposalDetail> = {}): SkillProposalDetail => ({
  ...proposal(),
  procedure_md: 'When this skill applies, honor the correction the user gave.',
  source_excerpt: '',
  diff: DIFF,
  version: 2,
  ...over,
})

describe('refine pill label', () => {
  it('names the stumble for every trigger the backend can emit', () => {
    for (const t of ['correction', 'failure_retry', 'rejection']) {
      expect(TRIGGER_LABEL[t], t).toBeTruthy()
      expect(refinePillLabel(t)).toBe(`Refine · ${TRIGGER_LABEL[t]}`)
    }
  })

  it('falls back to plain refine for an absent or unknown trigger', () => {
    expect(refinePillLabel(undefined)).toBe('Refine')
    expect(refinePillLabel('')).toBe('Refine')
    expect(refinePillLabel('some_future_trigger')).toBe('Refine')
  })

  it('produces DISTINCT labels per trigger', () => {
    const labels = new Set(['correction', 'failure_retry', 'rejection'].map(refinePillLabel))
    expect(labels.size).toBe(3)
  })
})

describe('unified diff renderer', () => {
  it('maps added, removed, hunk and header lines to distinct tokens', () => {
    const added = diffLineColor('+new')
    const removed = diffLineColor('-old')
    const hunk = diffLineColor('@@ -1,2 +1,3 @@')
    expect(new Set([added, removed, hunk]).size).toBe(3)
    expect(diffLineColor('+++ a/SKILL.md')).not.toBe(added)
    expect(diffLineColor('--- a/SKILL.md')).not.toBe(removed)
    expect(diffLineColor(' context')).toBeUndefined()
  })

  it('renders the patch as TEXT, so markup inside it cannot become DOM', () => {
    const hostile = '+<img src=x onerror=alert(1)>\n+**not bold**'
    const { container } = render(<UnifiedDiff patch={hostile} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('strong')).toBeNull()
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>')
    expect(container.textContent).toContain('**not bold**')
  })

  it('keeps a line box for a blank patch line', () => {
    const { container } = render(<UnifiedDiff patch={'a\n\nb'} />)
    const rows = container.querySelectorAll('pre > div')
    expect(rows.length).toBe(3)
    expect(rows[1].textContent).not.toBe('')
  })
})

describe('SkillProposals refine row', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows the diff, the version it would create, and the reason on the pill', async () => {
    vi.spyOn(api, 'skillProposals').mockResolvedValue({ proposals: [proposal()], lastReview: null })
    vi.spyOn(api, 'skillProposalDetail').mockResolvedValue(detail())

    render(<SkillProposals />)
    const row = await screen.findByText('release-flow')
    expect(screen.getByText('Refine · you corrected it')).toBeTruthy()

    await userEvent.click(row)
    const heading = await waitFor(() => screen.getByText(/^Change to release-flow/))
    expect(heading.textContent).toMatch(/refinement v2/i)
    expect(screen.getByText('+## Refinement v2 (2026-08-25, from a correction)')).toBeTruthy()
    expect(screen.queryByText('Procedure')).toBeNull()
  })

  it('says plainly when a refine has NO diff, instead of showing an empty change', async () => {
    vi.spyOn(api, 'skillProposals').mockResolvedValue({ proposals: [proposal()], lastReview: null })
    vi.spyOn(api, 'skillProposalDetail').mockResolvedValue(detail({ diff: '', version: 0 }))

    render(<SkillProposals />)
    await userEvent.click(await screen.findByText('release-flow'))
    await waitFor(() => expect(screen.getByText(/no longer installed/i)).toBeTruthy())
    expect(screen.getByText('Procedure')).toBeTruthy()
  })

  it('names the VERSION in the accept confirmation', async () => {
    vi.spyOn(api, 'skillProposals').mockResolvedValue({ proposals: [proposal()], lastReview: null })
    vi.spyOn(api, 'acceptSkillProposal').mockResolvedValue({ ok: true, name: 'release-flow', version: 2 })

    render(<SkillProposals />)
    await screen.findByText('release-flow')
    await userEvent.click(screen.getByRole('button', { name: /accept/i }))
    await waitFor(() => expect(screen.getByText(/refinement v2/i)).toBeTruthy())
  })

  it('omits the version for a kind=new accept, which creates rather than versions', async () => {
    vi.spyOn(api, 'skillProposals').mockResolvedValue({
      proposals: [proposal({ kind: 'new', trigger: '', refine_target: '' })],
      lastReview: null,
    })
    vi.spyOn(api, 'acceptSkillProposal').mockResolvedValue({ ok: true, name: 'auto/release-flow', version: 0 })

    render(<SkillProposals />)
    await screen.findByText('release-flow')
    await userEvent.click(screen.getByRole('button', { name: /accept/i }))
    await waitFor(() => expect(screen.getByText(/Accepted → auto\/release-flow/)).toBeTruthy())
    expect(screen.queryByText(/refinement v/i)).toBeNull()
  })
})
