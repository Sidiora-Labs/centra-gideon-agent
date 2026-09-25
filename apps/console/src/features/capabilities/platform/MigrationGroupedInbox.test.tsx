import '@testing-library/jest-dom/vitest'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import Migration from './Migration'

vi.mock('../../../shared/data/gatewayRequest', () => ({ requestJson: vi.fn(async () => ({ receipts: [] })) }))
afterEach(cleanup)

it('states immutable inbox group retention without archive rollback claims', async () => {
  render(<Migration />)
  expect(await screen.findByText('No archive imports recorded.')).toBeVisible()
  expect(screen.getByText(/independent canonical-record, immutable inbox, and song groups/)).toBeVisible()
  expect(screen.getByText(/without claiming archive-wide rollback or deleting immutable inbox events/)).toBeVisible()
})
