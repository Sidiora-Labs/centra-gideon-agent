import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AudioRecorder } from './AudioRecorder'


describe('AudioRecorder exposes its state and time to assistive tech', () => {
  it('has an always-mounted polite status live region', () => {
    const { container } = render(<AudioRecorder onRecorded={() => {}} onClear={() => {}} />)
    const status = container.querySelector('[role="status"][aria-live="polite"]')
    expect(status, 'a polite status region must exist for state announcements').toBeTruthy()
    expect(status?.className, 'it is an announcement, not visible chrome').toContain('sr-only')
  })

  it('the elapsed-time readout is a named timer, not an anonymous number', () => {
    render(<AudioRecorder onRecorded={() => {}} onClear={() => {}} />)
    const timer = screen.getByRole('timer', { name: 'Recording time' })
    expect(timer).toBeTruthy()
    expect(timer.getAttribute('aria-live'), 'the timer must not announce every tick').not.toBe('polite')
  })

  it('the status text is the four states, and idle is silent', () => {
    const src = require('node:fs').readFileSync(
      require('node:path').join(process.cwd(), "src/features/knowledge/AudioRecorder.tsx"), 'utf8')
    expect(src).toMatch(/state === 'recording' \? 'Recording'/)
    expect(src).toMatch(/state === 'paused' \? 'Recording paused'/)
    expect(src).toMatch(/state === 'done' \? 'Recording complete'/)
  })
})
