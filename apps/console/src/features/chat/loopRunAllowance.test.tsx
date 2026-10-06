import { it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ApprovalCard } from './ApprovalCard'
import { hydrateTurns } from './chatTypes'
import type { ApprovalSegment } from './chatTypes'

it('renders the host-issued run scope and configured duration from actual permission metadata', () => {
  const turns = hydrateTurns([{ role: 'permission', content: 'write_file', meta: { approval_id: 'loop-a:request-1', tool_kind: 'write', loop_run_offer: { loop_id: 'a934abcd', name: 'Release review', duration_seconds: 60, run_started_at: 123 } } }], false)
  const segment = turns[0].segments[0] as ApprovalSegment
  render(<ApprovalCard seg={segment} onAct={() => {}} />)
  fireEvent.click(screen.getByRole('tab', { name: 'This run' }))
  expect(screen.getByText(/All workers in.*Release review.*60 seconds/)).toBeTruthy()
})
