import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { ApprovalCard } from './ApprovalCard'
import { approvalOutcome, type ApprovalResolution } from './approvalOutcome'
import { hydrateTurns, type ApprovalSegment, type HistMsg } from './chatTypes'


const ICON_CLASS = { check: 'lucide-check', ban: 'lucide-ban', unknown: 'lucide-circle-question-mark' } as const

function paintSettled(resolved: string): { icon: string | null; text: string } {
  const seg: ApprovalSegment = { kind: 'approval', id: 'a1', tool: 'Terminal', resolved }
  const { container } = render(<ApprovalCard seg={seg} onAct={() => {}} />)
  const svg = container.querySelector('svg')
  return {
    icon: svg?.getAttribute('class')?.split(/\s+/).find((c) => c.startsWith('lucide-')) ?? null,
    text: container.textContent ?? '',
  }
}

const CASES: { resolved: ApprovalResolution; approved: boolean; says: string }[] = [
  { resolved: 'approved', approved: true, says: 'approved' },
  { resolved: 'trust', approved: true, says: 'this chat' },
  { resolved: 'trust_agent', approved: true, says: 'this agent' },
  { resolved: 'trust_agent_session', approved: true, says: 'this chat' },
  { resolved: 'trust_reads', approved: true, says: 'reads' },
  { resolved: 'yolo', approved: true, says: 'YOLO' },
  { resolved: 'rejected', approved: false, says: 'denied' },
]

describe('approvalOutcome mapping', () => {
  it('renders every trust/YOLO grant as an APPROVAL, never as denied (#541)', () => {
    for (const c of CASES.filter((x) => x.approved)) {
      const o = approvalOutcome(c.resolved)
      expect(o.icon.name ?? o.icon.displayName, c.resolved).not.toBe('Ban')
      expect(o.tone, c.resolved).toBe('var(--color-ok)')
      expect(o.label, c.resolved).not.toContain('denied')
    }
  })

  it('names the SCOPE so an auditor can tell a standing grant from a confirmation', () => {
    expect(approvalOutcome('approved').label).toBe('approved')
    for (const r of ['trust', 'trust_reads', 'yolo'] as const) {
      expect(approvalOutcome(r).label, r).toContain('auto-approved')
    }
    const labels = (['approved', 'trust', 'trust_reads', 'yolo'] as const).map((r) => approvalOutcome(r).label)
    expect(new Set(labels).size).toBe(labels.length)
  })

  it('maps a denial to denied', () => {
    expect(approvalOutcome('rejected').label).toBe('denied')
  })

  it('does not read an UNKNOWN outcome as denied or as approved', () => {
    const o = approvalOutcome('trust_project')
    expect(o.label).not.toContain('denied')
    expect(o.label).not.toContain('approved')
    expect(o.label).toContain('trust_project')
    expect(o.tone).not.toBe('var(--color-ok)')
  })

  it('caps a pathological persisted value so it cannot stretch the chat column', () => {
    expect(approvalOutcome('x'.repeat(500)).label.length).toBeLessThan(60)
  })
})

describe('ApprovalCard settled line', () => {
  it('shows a check for every approval and a ban only for a denial (#541)', () => {
    for (const c of CASES) {
      const { icon, text } = paintSettled(c.resolved)
      expect(icon, c.resolved).toBe(c.approved ? ICON_CLASS.check : ICON_CLASS.ban)
      expect(text, c.resolved).toContain('Terminal')
      expect(text, c.resolved).toContain(c.says)
      if (c.approved) expect(text, c.resolved).not.toContain('denied')
    }
  })

  it('shows neither a check nor a ban for an unknown outcome', () => {
    const { icon, text } = paintSettled('trust_project')
    expect(icon).toBe(ICON_CLASS.unknown)
    expect(text).not.toContain('denied')
  })

  it('still renders the actionable picker while PENDING', () => {
    const { container } = render(<ApprovalCard seg={{ kind: 'approval', id: 'a1', tool: 'Terminal' }} onAct={() => {}} />)
    const labels = [...container.querySelectorAll('button')].map((b) => b.textContent?.trim())
    expect(labels).toEqual(['Just this once', 'This chat', 'Allow', 'Deny'])
  })
})

describe('history hydration parity', () => {
  const permRow = (resolved?: string): HistMsg => ({
    role: 'permission', content: 'Terminal',
    meta: { approval_id: 'a1', tool: 'Terminal', ...(resolved ? { resolved } : {}) },
  })
  const segOf = (m: HistMsg) => hydrateTurns([m], false)[0].segments[0] as ApprovalSegment

  it('carries every outcome through reload unchanged, so a reloaded transcript matches the live one', () => {
    for (const c of CASES) {
      expect(segOf(permRow(c.resolved)).resolved, c.resolved).toBe(c.resolved)
      expect(paintSettled(segOf(permRow(c.resolved)).resolved!).icon, c.resolved)
        .toBe(c.approved ? ICON_CLASS.check : ICON_CLASS.ban)
    }
  })

  it('preserves an unknown outcome instead of downgrading it to pending', () => {
    expect(segOf(permRow('trust_project')).resolved).toBe('trust_project')
  })

  it('leaves a genuinely pending row unresolved', () => {
    expect(segOf(permRow(undefined)).resolved).toBeUndefined()
    expect(segOf({ role: 'permission', content: 'Terminal', meta: { approval_id: 'a1', resolved: '' } }).resolved).toBeUndefined()
  })
})
