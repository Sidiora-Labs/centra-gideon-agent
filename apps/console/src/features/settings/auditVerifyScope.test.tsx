import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { AuditPage } from '../../shared/data/api'
import { AuditPanel, verifiedScope, capped } from './AuditPanel'


const auditEvents = vi.fn()
const auditVerify = vi.fn()
const selRotate = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    auditEvents: (...a: unknown[]) => auditEvents(...a),
    auditVerify: (...a: unknown[]) => auditVerify(...a),
    selRotate: (...a: unknown[]) => selRotate(...a),
  },
}))
vi.mock('../../shared/data/data', () => ({ invalidateKeys: vi.fn() }))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))

const page = () => ({ events: [], next_cursor: null, outcome_families: [], total: 0 } as unknown as AuditPage)

const PANEL = join(process.cwd(), "src/features/settings/AuditPanel.tsx")
const WIDGETS = join(process.cwd(), "src/features/settings/settingsWidgets.tsx")
const HANDLER = join(process.cwd(), "../../runtime/gideon/interfaces/dashboard/handlers/security_audit.py")
const strip = (p: string) => readFileSync(p, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the verdict names the scope it actually covered', () => {
  it('a cap that BIT is reported as a window', () => {
    expect(verifiedScope({ checked: 5000, windowed: true, window: 5000 })).toBe('the last 5,000 events')
    expect(capped({ checked: 5000, windowed: true, window: 5000 })).toBe(true)
  })

  it('a cap that never bit is reported as complete — the understatement is a defect too', () => {
    expect(verifiedScope({ checked: 43, windowed: true, window: 5000 })).toBe('all 43 events')
    expect(capped({ checked: 43, windowed: true, window: 5000 })).toBe(false)
  })

  it('an exhaustive check says all, and the count is grouped', () => {
    expect(verifiedScope({ checked: 1234567, windowed: false, window: null })).toBe('all 1,234,567 events')
    expect(capped({ checked: 1234567, windowed: false, window: null })).toBe(false)
  })

  it('a server that sends no window cannot be made to claim one', () => {
    expect(capped({ checked: 5000, windowed: true })).toBe(false)
    expect(verifiedScope({ checked: 5000, windowed: true })).toBe('all 5,000 events')
  })

  it('one phrase, two surfaces — the bento tile does not re-word it', () => {
    const w = strip(WIDGETS)
    expect(w).toMatch(/\{verifiedScope\(v\)\} verified/)
    expect(w, 'the bare count must not come back').not.toMatch(/\{v\.checked\} events verified/)
    expect(w).toMatch(/import \{ verifiedScope \} from '\.\/AuditPanel'/)
  })

  it('the server sends the cap it applied, not just that one existed', () => {
    const py = readFileSync(HANDLER, 'utf8')
    expect(py, 'the window size is what makes the distinction possible')
      .toMatch(/"window": None if full else _VERIFY_WINDOW,/)
    expect(py, 'and the older flag stays — it is what says a cap was set at all')
      .toMatch(/"windowed": not full,/)
  })
})

describe('the panel says what was left out, and where the whole check lives', () => {
  beforeEach(() => { vi.clearAllMocks(); auditEvents.mockResolvedValue(page()) })

  it('a capped pass names the window AND the offline command', async () => {
    auditVerify.mockResolvedValue({ ok: true, checked: 5000, valid: 5000, tampered: 0, windowed: true, window: 5000 })
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Verify$/ }))
    await waitFor(() => expect(screen.getByText(/the last 5,000 events verified/)).toBeTruthy())
    expect(screen.getByText(/Older entries were not checked/)).toBeTruthy()
    expect(screen.getByText('gideon security verify')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /whole log|verify everything|full/i })).toBeNull()
  })

  it('an uncapped pass says nothing about windows — there is nothing to escalate', async () => {
    auditVerify.mockResolvedValue({ ok: true, checked: 43, valid: 43, tampered: 0, windowed: true, window: 5000 })
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Verify$/ }))
    await waitFor(() => expect(screen.getByText(/all 43 events verified/)).toBeTruthy())
    expect(screen.queryByText(/Older entries were not checked/)).toBeNull()
    expect(screen.queryByText('gideon security verify')).toBeNull()
  })

  it('a BROKEN chain states its scope too, and still points at the full check', async () => {
    auditVerify.mockResolvedValue({ ok: false, checked: 5000, valid: 4998, tampered: 2, windowed: true, window: 5000 })
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Verify$/ }))
    await waitFor(() => expect(screen.getByText(/2 of the last 5,000 events altered/)).toBeTruthy())
    expect(screen.getByText('gideon security verify')).toBeTruthy()
  })

  it('a request failure says verification did not run instead of fabricating a broken verdict', async () => {
    auditVerify.mockRejectedValue(new Error('Gateway unavailable'))
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /^Verify$/ }))
    await waitFor(() => expect(screen.getByText(/Verification did not run/)).toBeTruthy())
    expect(screen.getByText(/Gateway unavailable/)).toBeTruthy()
    expect(screen.getByText(/try Verify again/)).toBeTruthy()
    expect(screen.queryByText(/Chain broken/)).toBeNull()
    expect(screen.queryByText(/Chain intact/)).toBeNull()
  })

  it('the verify request stays the windowed one — the browser never walks the log', () => {
    const src = strip(PANEL)
    expect(src, 'no call site may pass full=true from the UI').not.toMatch(/auditVerify\(true\)/)
    expect(src, 'and the CLI is named where a button would otherwise go')
      .toMatch(/gideon security verify/)
  })
})
