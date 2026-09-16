import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { IdentityReportPanel } from './IdentityReportPanel'
import { api, type IdentityReportView } from '../../shared/data/api'


function report(overrides: Partial<IdentityReportView> = {}): IdentityReportView {
  return {
    period: { window_days: 30, since: '2026-07-21T12:00:00+00:00', until: '2026-08-20T12:00:00+00:00' },
    window_days: 30,
    generated_at: '2026-08-20T12:00:00+00:00',
    total: 1,
    facets: { count: 1, items: [{ text: 'prefers terse replies', cls: 'style', stability: 0.8, state: 'Active', updated_at: '', pinned: false }] },
    lessons: { count: 0, items: [] },
    skills: { count: 0, items: [] },
    proposals: { count: 0, items: [] },
    memory: {},
    narrative: '',
    narrative_status: 'skipped',
    markdown: '# How I\'ve adapted to you\n',
    cadence: 'monthly',
    ...overrides,
  }
}

const PANEL = join(dirname(fileURLToPath(import.meta.url)), 'IdentityReportPanel.tsx')

const strip = () => screen.getByRole('tablist', { name: 'Write one automatically' })

describe('the identity report cadence control', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('offers every cadence the backend accepts, including off', () => {
    render(<IdentityReportPanel report={report()} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    const labels = Array.from(strip().querySelectorAll('[role="tab"]')).map((t) => t.textContent?.trim())
    expect(labels).toEqual(['Monthly', 'Weekly', 'Off'])
    expect(screen.getByRole('tab', { name: 'Monthly' }).getAttribute('aria-selected')).toBe('true')
  })

  it('shows the server value as active, not the default', () => {
    render(<IdentityReportPanel report={report({ cadence: 'weekly' })} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    expect(screen.getByRole('tab', { name: 'Weekly' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: 'Monthly' }).getAttribute('aria-selected')).toBe('false')
  })

  it('writes learning.identity_report_cadence and re-reads the report', async () => {
    const spy = vi.spyOn(api, 'patchConfig').mockResolvedValue({})
    const onRetry = vi.fn()

    render(<IdentityReportPanel report={report()} error={undefined} onRetry={onRetry} onDelivered={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Weekly' }))

    await waitFor(() => expect(spy).toHaveBeenCalledWith('learning.identity_report_cadence', 'weekly'))
    await waitFor(() => expect(onRetry).toHaveBeenCalled())
    expect(screen.getByRole('tab', { name: 'Weekly' }).getAttribute('aria-selected')).toBe('true')
  })

  it('says what off means instead of leaving the panel looking broken', () => {
    render(<IdentityReportPanel report={report({ cadence: 'off' })} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    expect(screen.getByText(/Nothing is scheduled/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Write it up/ }).getAttribute('aria-disabled')).toBeNull()
  })

  it('REVERTS the strip when the save fails, and names the control in the error', async () => {
    const spy = vi.spyOn(api, 'patchConfig').mockRejectedValue(new Error('gateway said no'))

    render(<IdentityReportPanel report={report()} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Off' }))

    await waitFor(() => expect(spy).toHaveBeenCalled())
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Write one automatically')
    expect(alert.textContent).toContain('gateway said no')
    expect(screen.getByRole('tab', { name: 'Monthly' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: 'Off' }).getAttribute('aria-selected')).toBe('false')
  })

  it('renders NO control when the server could not read the config', () => {
    render(<IdentityReportPanel report={report({ cadence: '' })} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    expect(screen.queryByRole('tablist', { name: 'Write one automatically' })).toBeNull()
    expect(screen.getByText(/Your settings could not be read/)).toBeTruthy()
    expect(screen.getByText("How I've adapted to you")).toBeTruthy()
  })

  it('uses one wording for the visible label and the accessible name', () => {
    render(<IdentityReportPanel report={report()} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    const visible = screen.getByTestId('cadence-label').textContent?.trim()
    expect(visible).toBe(strip().getAttribute('aria-label'))
  })

  it('does not also ship a second on/off switch', () => {
    const src = readFileSync(PANEL, 'utf8')
    expect(src).not.toContain('identity_report_enabled')
  })
})
