import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { DiagnosticsPanel } from './DiagnosticsPanel'


const logLevel = vi.fn()
const setLogLevel = vi.fn()
const logsUrl = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    logLevel: (...a: unknown[]) => logLevel(...a),
    setLogLevel: (...a: unknown[]) => setLogLevel(...a),
    logsUrl: (...a: unknown[]) => logsUrl(...a),
  },
}))

describe('the two level pickers are distinguishable by sight, not only by name', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    logLevel.mockResolvedValue('WARNING')
    logsUrl.mockReturnValue('http://127.0.0.1:0/api/logs?lines=300')
  })

  it('each group still announces its own dimension', async () => {
    render(<DiagnosticsPanel />)
    await waitFor(() => expect(screen.getByLabelText('Backend log level: WARNING')).toBeTruthy())
    for (const level of ['DEBUG', 'INFO', 'WARNING', 'ERROR']) {
      expect(screen.getByLabelText(`Backend log level: ${level}`), level).toBeTruthy()
      expect(screen.getByLabelText(`Show ${level} and above`), level).toBeTruthy()
    }
  })

  it('exactly one option is pressed in each group, and they are set independently', async () => {
    render(<DiagnosticsPanel />)
    await waitFor(() => expect(screen.getByLabelText('Backend log level: WARNING').getAttribute('aria-pressed')).toBe('true'))
    expect(screen.getByLabelText('Show DEBUG and above').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByLabelText('Show WARNING and above').getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByLabelText('Backend log level: DEBUG').getAttribute('aria-pressed')).toBe('false')
  })

  it('the view filter carries a VISIBLE label, adjacent to its own pills', async () => {
    render(<DiagnosticsPanel />)
    await waitFor(() => expect(screen.getByLabelText('Show DEBUG and above')).toBeTruthy())
    const label = screen.getByText('Show', { selector: 'span' })
    const pills = screen.getByLabelText('Show DEBUG and above').parentElement!
    expect(label.nextElementSibling, 'the label sits immediately before the pill group').toBe(pills)
    expect(label.className).toContain('hidden')
    expect(label.className).toContain('sm:inline')
  })

  it('the backend group keeps its own visible echo, so the two rows read differently', async () => {
    render(<DiagnosticsPanel />)
    await waitFor(() => expect(screen.getByText('Current:')).toBeTruthy())
    expect(screen.getByText('WARNING', { selector: 'strong' })).toBeTruthy()
  })
})
