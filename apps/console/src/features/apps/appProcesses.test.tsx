import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ProcessCards } from './appProcesses'
import type { AppProcessStatus } from '../../shared/data/api'

describe('native process cards', () => {
  it('shows bounded output and clears prior failures when running or owner stopped', () => {
    const status: AppProcessStatus = {
      backendRunning: false, backendPort: null,
      backendExit: { pid: 1, exitCode: 9, ended: 'exit', endedAt: '2026-10-06T12:00:00Z', cause: 'Backend failed', lines: ['stderr: failure'] },
      workers: [{ name: 'indexer', running: false, state: 'exited', reason: '', exit: null }],
      engine: { running: true, exit: null },
    }
    const { rerender } = render(<ProcessCards status={status} hasBackend />)
    expect(screen.getByText(/Exited with code 9/)).toBeTruthy()
    expect(screen.getByText('Recent output')).toBeTruthy()
    expect(screen.getByText('Worker · indexer')).toBeTruthy()
    expect(screen.getByText('Engine')).toBeTruthy()
    rerender(<ProcessCards status={{ ...status, backendRunning: true, backendPort: 4321 }} hasBackend />)
    expect(screen.getByText('Running on port 4321')).toBeTruthy()
    expect(screen.queryByText('Backend failed')).toBeNull()
    rerender(<ProcessCards status={{ backendRunning: false, backendPort: null, backendExit: null }} hasBackend />)
    expect(screen.getByText('Not running')).toBeTruthy()
    expect(screen.queryByText(/Exited with code/)).toBeNull()
  })
})
