import '@testing-library/jest-dom/vitest'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import Migration from './Migration'

vi.mock('../../../shared/data/gatewayRequest', () => ({ requestJson: vi.fn(async () => ({ receipts: [] })) }))
afterEach(cleanup)

it('states independent group completion and exact reviewed retry semantics', async () => {
  render(<Migration />)
  expect(await screen.findByText('No archive imports recorded.')).toBeVisible()
  expect(screen.getByText(/Mixed song archives commit as independent canonical-record and song groups/)).toBeVisible()
  expect(screen.getByText(/Completed groups are retained, unfinished groups resume only from the exact reviewed archive/)).toBeVisible()
  expect(screen.getByText(/without claiming archive-wide rollback/)).toBeVisible()
})
