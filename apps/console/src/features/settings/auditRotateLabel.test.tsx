import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { AuditPage } from '../../shared/data/api'
import { AuditPanel } from './AuditPanel'


const auditEvents = vi.fn()
const auditVerify = vi.fn()
const selRotate = vi.fn()
const confirm = vi.fn()
const notify = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    auditEvents: (...a: unknown[]) => auditEvents(...a),
    auditVerify: (...a: unknown[]) => auditVerify(...a),
    selRotate: (...a: unknown[]) => selRotate(...a),
  },
}))
vi.mock('../../shared/data/data', () => ({ invalidateKeys: vi.fn() }))
vi.mock('../../app/shell/appSdk', () => ({ notify: (...a: unknown[]) => notify(...a) }))
vi.mock('../../shared/ui/dialog', () => ({ confirm: (...a: unknown[]) => confirm(...a) }))

const page = () => ({ events: [], next_cursor: null, outcome_families: [], total: 0 } as unknown as AuditPage)

const PANEL = join(process.cwd(), "src/features/settings/AuditPanel.tsx")
const strip = (p: string) => readFileSync(p, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the rotate control makes no promise about a signing key', () => {
  it('the confirm copy names an archive, never a key promise — vacuity floor: the block must be locatable', () => {
    const block = strip(PANEL).match(/confirm\(\{[\s\S]*?\}\)/)
    expect(block, 'the rotate confirm dialog block must be locatable').not.toBeNull()
    const copy = block![0]

    expect(copy, 'the copy names the real action: archive').toMatch(/archive/i)

    expect(copy, 'no verifiable-under-a-key promise').not.toMatch(/verifiable/i)
    expect(copy, 'no claim that a key is rotated').not.toMatch(/rotate/i)

    const clauses = copy.match(/[^.?!]*\b(?:key|signing)\b[^.?!]*/gi) ?? []
    for (const c of clauses)
      expect(c.trim(), `key/signing mentioned only as unchanged: "${c.trim()}"`).toMatch(
        /unchang|not\s+(?:changed|rotated|touched)/i,
      )
  })
})

describe('a successful rotate tells the user where the log was archived', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auditEvents.mockResolvedValue(page())
    confirm.mockResolvedValue(true)
  })

  it('surfaces the archive file basename (not the full path) in a success notification', async () => {
    selRotate.mockResolvedValue({
      rotated: true,
      entries_before: 3,
      entries_after: 0,
      archive_path: '/home/u/.gideon/security_events.20260902T010203Z.bak.jsonl',
    })
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Rotate$/ }))
    await waitFor(() => expect(selRotate).toHaveBeenCalled())
    await waitFor(() => expect(notify).toHaveBeenCalled())
    const [message, level] = notify.mock.calls.at(-1) as [string, string]
    expect(message).toContain('security_events.20260902T010203Z.bak.jsonl')
    expect(message, 'basename only — never the containing directory').not.toContain('/home/u/')
    expect(level).toBe('success')
  })

  it('still confirms a reset when the payload carries no archive path', async () => {
    selRotate.mockResolvedValue({ rotated: true, entries_before: 0, entries_after: 0, archive_path: '' })
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Rotate$/ }))
    await waitFor(() => expect(notify).toHaveBeenCalled())
    const [message, level] = notify.mock.calls.at(-1) as [string, string]
    expect(level).toBe('success')
    expect(message).toMatch(/reset|fresh chain/i)
  })
})
