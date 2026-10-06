import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { JobOutcome } from './DurabilityPanel'

afterEach(cleanup)

it('shows the actual failed run beside the retained last successful run', () => {
  render(<JobOutcome label="Nightly snapshot" job={{ last_run: 20, last_success: 10, due: true, due_in_secs: 0, ok: false, problem: {code:'disk_full', message:'The snapshot was not taken: the disk is full.', remedy:'Free some space.', since:20, failures:2} }} />)
  expect(screen.getByText(/The snapshot was not taken/)).toHaveTextContent('The last 2 runs failed.')
  expect(screen.getByText(/Last successful run:/)).toBeInTheDocument()
})

it('does not invent a successful run for a fresh installation', () => {
  render(<JobOutcome label="Incremental export" job={{last_run:0,due:true,due_in_secs:0,ok:null,last_success:0,problem:null}} />)
  expect(screen.getByText(/never/)).toBeInTheDocument()
  expect(screen.queryByText(/Last successful run:/)).not.toBeInTheDocument()
})
