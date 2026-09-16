import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ProposalCard, UpdatePreview } from './PacksPanel'
import type { PackProposalRec, PackUpdateRec } from '../../shared/data/api'

vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))


const proposal: PackProposalRec = {
  project_id: 'proj-tf',
  pack: 'infra-ops',
  displayName: 'Infra Ops',
  description: 'Review infrastructure-as-code changes honestly.',
  version: '1.0.0',
  confidence: 0.68,
  matches: [
    {
      label: 'Terraform project',
      confidence: 0.68,
      declared_confidence: 0.9,
      matched_globs: ['*.tf'],
      matched_signals: ['terraform {', 'provider "'],
      declared_globs: ['*.tf', '*.tfvars'],
      declared_signals: ['terraform {', 'provider "'],
      evidence: ['main.tf', 'modules/vpc/main.tf'],
    },
  ],
  files_scanned: 42,
  inspect: {
    name: 'infra-ops',
    version: '1.0.0',
    blocked: false,
    needs_consent: false,
    components: [
      { kind: 'skill', orig_id: 'infra-plan-review', target_id: 'infra-plan-review', verdict: 'clean' },
      { kind: 'template', orig_id: 'infra-change-review', target_id: 'infra-change-review', verdict: 'clean' },
    ],
    requirements: [],
    staged_triggers: [],
  },
  inspect_error: '',
}

const renderCard = (p: PackProposalRec = proposal) => {
  const onInstall = vi.fn()
  const onReject = vi.fn()
  const r = render(<ProposalCard proposal={p} busy={false} onInstall={onInstall} onReject={onReject} />)
  return { ...r, onInstall, onReject }
}

describe('propose-only', () => {
  it('offers both an accept and a decline', () => {
    renderCard()
    expect(screen.getByRole('button', { name: 'Install' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Not for this project' })).toBeTruthy()
  })

  it('installs only when the install button is pressed', () => {
    const { onInstall, onReject } = renderCard()
    expect(onInstall).not.toHaveBeenCalled()
    screen.getByRole('button', { name: 'Install' }).click()
    expect(onInstall).toHaveBeenCalledWith(proposal)
    expect(onReject).not.toHaveBeenCalled()
  })

  it('reports the decline with the project AND the pack, so the memory is scoped', () => {
    const { onReject } = renderCard()
    screen.getByRole('button', { name: 'Not for this project' }).click()
    expect(onReject).toHaveBeenCalledWith(expect.objectContaining({ project_id: 'proj-tf', pack: 'infra-ops' }))
  })
})

describe('confidence carries its derivation', () => {
  it('shows the score as a percentage', () => {
    expect(renderCard().container.textContent).toContain('68% match')
  })

  it('shows how much of the rule matched, and the declared ceiling', () => {
    const t = renderCard().container.textContent ?? ''
    expect(t).toContain('1 of 2 file patterns')
    expect(t).toContain('2 of 2 content signals')
    expect(t).toContain('declared ceiling of 90%')
  })

  it('names the shape it thinks it found', () => {
    expect(renderCard().container.textContent).toContain('Looks like a terraform project')
  })

  it('shows example matched paths against the number of files scanned', () => {
    const t = renderCard().container.textContent ?? ''
    expect(t).toContain('main.tf')
    expect(t).toContain('modules/vpc/main.tf')
    expect(t).toContain('of 42 files scanned')
  })

  it('omits the content-signal clause for a rule that declares none', () => {
    const noSignals = {
      ...proposal,
      matches: [{ ...proposal.matches[0], declared_signals: [], matched_signals: [] }],
    }
    expect(renderCard(noSignals).container.textContent).not.toContain('content signals')
  })
})

describe('the §3.1 inspect report', () => {
  it('lists what installing would put on this machine', () => {
    const t = renderCard().container.textContent ?? ''
    expect(t).toContain('Would install')
    expect(t).toContain('skill:infra-plan-review')
    expect(t).toContain('template:infra-change-review')
  })

  it('renders without a report at all (project-create omits it)', () => {
    const t = renderCard({ ...proposal, inspect: null }).container.textContent ?? ''
    expect(t).toContain('Infra Ops')
    expect(t).not.toContain('Would install')
  })

  it('says why the preview is missing rather than staying silent', () => {
    const t = renderCard({ ...proposal, inspect: null, inspect_error: 'BundledPackError: bad tree' })
      .container.textContent ?? ''
    expect(t).toContain("Couldn't preview what this would install")
    expect(t).toContain('BundledPackError: bad tree')
  })

  it('survives a match list that is empty', () => {
    const t = renderCard({ ...proposal, matches: [] }).container.textContent ?? ''
    expect(t).toContain('Infra Ops')
  })
})


const update: PackUpdateRec = {
  pack: 'infra-ops',
  from_version: '1.0.0',
  to_version: '1.1.0',
  applied: false,
  components: [
    { ref: 'skill:infra-drift-audit', action: 'overwrite', reason: 'pack-owned and unmodified since install', pack_path: 'skills/infra-drift-audit/SKILL.md', home_path: 'skills/infra-drift-audit' },
    { ref: 'skill:infra-plan-review', action: 'skip_drift', reason: 'edited since install (content hash differs from the install lock) — your version was kept, the pack\'s update was not applied', pack_path: 'skills/infra-plan-review/SKILL.md', home_path: 'skills/infra-plan-review' },
    { ref: 'template:infra-change-review', action: 'skip_not_pack_owned', reason: "'templates/infra-change-review.json' matches no pack_owned pattern", pack_path: 'templates/infra-change-review.json', home_path: '' },
  ],
  drift_notes: ['skill:infra-plan-review: edited since install'],
  overwritten: ['skill:infra-drift-audit'],
  skipped: ['skill:infra-plan-review', 'template:infra-change-review'],
}

describe('the update preview makes the skip visible', () => {
  it('names each kept component with the reason it was kept', () => {
    const t = render(<UpdatePreview update={update} busy={false} onApply={vi.fn()} />).container.textContent ?? ''
    expect(t).toContain('skill:infra-plan-review')
    expect(t).toContain('edited since install')
    expect(t).toContain('your version was kept')
  })

  it('tones the kept component as a warning so it is not read as a success', () => {
    const { container } = render(<UpdatePreview update={update} busy={false} onApply={vi.fn()} />)
    const warned = [...container.querySelectorAll('.text-warn')].map((e) => e.textContent)
    expect(warned).toContain('skill:infra-plan-review')
    expect(warned).not.toContain('skill:infra-drift-audit')
  })

  it('separates "you edited it" from "the pack does not own it"', () => {
    const t = render(<UpdatePreview update={update} busy={false} onApply={vi.fn()} />).container.textContent ?? ''
    expect(t).toContain('Not owned by this pack, so untouched: template:infra-change-review')
  })

  it('shows the version transition and the counts', () => {
    const t = render(<UpdatePreview update={update} busy={false} onApply={vi.fn()} />).container.textContent ?? ''
    expect(t).toContain('1.0.0 → 1.1.0')
    expect(t).toContain('1 to replace, 2 to keep')
  })

  it('offers Apply only while the update has NOT been applied', () => {
    render(<UpdatePreview update={update} busy={false} onApply={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Apply update' })).toBeTruthy()
  })

  it('drops the Apply button once applied, and says Updated', () => {
    const { container } = render(<UpdatePreview update={{ ...update, applied: true }} busy={false} onApply={vi.fn()} />)
    expect(container.querySelector('button')).toBeNull()
    expect(container.textContent).toContain('Updated')
    expect(container.textContent).toContain('Replaced')
  })

  it('offers no Apply button when there is nothing to replace', () => {
    const nothing: PackUpdateRec = { ...update, overwritten: [], components: update.components.filter((c) => c.action !== 'overwrite') }
    const { container } = render(<UpdatePreview update={nothing} busy={false} onApply={vi.fn()} />)
    expect(container.querySelector('button')).toBeNull()
  })
})
