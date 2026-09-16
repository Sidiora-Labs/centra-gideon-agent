import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, act, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ProposalsLens } from './ProposalsLens'
import type { InboxItem } from '../../shared/data/api'

//    region was created at the same moment its text appeared. `ResultAnnouncement` records why that

const ITEM = (id: string, title: string): InboxItem => ({
  id, channel: '', channel_name: '', message: title, sender_id: '', sender_name: '',
  item_kind: 'proposal', status: 'pending',
  refs: { proposal: { title, apply: { skill_promotion: { name: 'x' } }, preview: 'p', provenance: 'learning' } },
} as unknown as InboxItem)

const applyInboxProposal = vi.fn((_id: string, _edited?: unknown) => Promise.resolve({ ok: true }))
vi.mock('../../shared/data/api', () => ({ api: { applyInboxProposal: (i: string, e?: unknown) => applyInboxProposal(i, e) } }))

const outcomeNodes = (c: HTMLElement) => [...c.querySelectorAll('[role="status"][aria-live="polite"]')]

describe('proposal apply feedback is announced', () => {
  beforeEach(() => {
    applyInboxProposal.mockClear()
    applyInboxProposal.mockImplementation(() => Promise.resolve({ ok: true }))
  })

  it('every row mounts a polite region, empty before any apply', () => {
    const { container } = render(<ProposalsLens items={[ITEM('a', 'One'), ITEM('b', 'Two')]} onChanged={() => {}} />)
    const regions = outcomeNodes(container)
    expect(regions.length, 'one region per row, present at rest').toBe(2)
    for (const r of regions) {
      expect(r.textContent).toBe('')
      expect(r.className, 'empty costs no layout').toContain('sr-only')
    }
  })

  it('a successful apply announces, and the visible copy is not double-read', async () => {
    const { container } = render(<ProposalsLens items={[ITEM('a', 'One')]} onChanged={() => {}} />)
    const approve = await waitFor(() => screen.getByRole('button', { name: /^Approve$/ }))
    await act(async () => { approve.click() })
    await waitFor(() => expect(outcomeNodes(container)[0].textContent).toBe('Applied.'))
    const node = outcomeNodes(container)[0]
    expect(node.className, 'it stops being sr-only once filled').not.toContain('sr-only')
    const carriers = [...container.querySelectorAll('*')]
      .filter((e) => e.textContent === 'Applied.' && e.children.length === 0)
    expect(carriers.length, 'one sentence, one node — nothing read twice').toBe(1)
  })

  it('a failed apply announces the reason and that the item is still pending', async () => {
    applyInboxProposal.mockImplementation(() => Promise.resolve({ ok: false, error: 'dispatcher refused' }))
    const { container } = render(<ProposalsLens items={[ITEM('a', 'One')]} onChanged={() => {}} />)
    const approve = await waitFor(() => screen.getByRole('button', { name: /^Approve$/ }))
    await act(async () => { approve.click() })
    await waitFor(() => expect(outcomeNodes(container)[0].textContent)
      .toBe('Not applied — dispatcher refused. Still pending.'))
  })

  it('the region is not conditionally mounted — the shape that could not announce', () => {
    const src = readFileSync(join(process.cwd(), "src/features/inbox/ProposalsLens.tsx"), 'utf8')
    expect(src, 'role + polite live region present').toMatch(/role="status"\s*\n\s*aria-live="polite"/)
    expect(src, 'sr-only only while empty').toMatch(/: 'sr-only'\}/)
    expect(src, 'text driven by the outcome').toMatch(/\{outcome \? \(outcome\.ok \? 'Applied\.'/)
    expect(/\{outcome && \([\s\S]{0,160}role="status"/.test(src),
      'a conditionally mounted region is born with its content and is not reliably observed').toBe(false)
  })

  it('the edit error interrupts through FieldError, not a bare div', () => {
    const src = readFileSync(join(process.cwd(), "src/features/inbox/ProposalsLens.tsx"), 'utf8')
    expect(src).toMatch(/\{draftError && <FieldError>\{draftError\}<\/FieldError>\}/)
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
    expect(/text-error/.test(code), 'the vestigial token alias should be gone from CODE').toBe(false)
    const forms = readFileSync(join(process.cwd(), "src/shared/ui/forms.tsx"), 'utf8')
    expect(forms).toMatch(/export function FieldError[\s\S]{0,300}role="alert"/)
  })
})
