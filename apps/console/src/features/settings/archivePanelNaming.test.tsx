import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { api } from '../../shared/data/api'
import { ArchivePanel } from './ArchivePanel'

describe('ArchivePanel archive naming', () => {
  it('names the archived unit, its triggers, and its retention window', async () => {
    vi.spyOn(api, 'sessionArchives').mockResolvedValue([])

    render(<ArchivePanel />)

    const hint = await screen.findByText(/Dropped session-history lines from compaction or rotation/i)
    expect(hint.textContent).toContain('7-day retention window')
    expect(screen.getByText(/No dropped lines archived yet/i).textContent)
      .toContain('Lines dropped by compaction or rotation appear here for 7 days.')
  })
})
