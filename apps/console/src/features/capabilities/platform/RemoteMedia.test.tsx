import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import RemoteMedia from './RemoteMedia'

afterEach(() => vi.restoreAllMocks())

it('dispatches, refreshes and cancels accountable remote jobs', async () => {
  let item: Record<string, unknown> | undefined
  const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input); const body = init?.body ? JSON.parse(String(init.body)) : undefined
    if (init?.method === 'POST' && url.endsWith('/remote-media')) {
      expect(body.request.operation).toBe('image_generate'); expect(body.request.input.prompt).toBe('A lunar harbor')
      item = { id: 'execution-1', request_id: 'cover-1', peer_id: 'peer-1', remote_job_id: 'job-1', remote_state_revision: 1, status: 'queued', result: null }
      return new Response(JSON.stringify(item), { status: 202 })
    }
    if (url.endsWith('/refresh')) { item = { ...item, remote_state_revision: 2, status: 'running' }; return new Response(JSON.stringify(item)) }
    if (url.endsWith('/cancel')) { expect(body).toEqual({ state_revision: 2 }); item = { ...item, remote_state_revision: 3, status: 'cancel_requested' }; return new Response(JSON.stringify(item)) }
    return new Response(JSON.stringify({ peers: [{ id: 'peer-1', label: 'Render station' }], items: item ? [item] : [] }))
  })
  render(<RemoteMedia />)
  await screen.findByText('Render station')
  fireEvent.change(screen.getByLabelText('Request ID'), { target: { value: 'cover-1' } })
  fireEvent.change(screen.getByLabelText('Image prompt'), { target: { value: 'A lunar harbor' } })
  fireEvent.click(screen.getByRole('button', { name: 'Dispatch remote job' }))
  await screen.findByText(/cover-1/)
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await screen.findByText(/running/)
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  await screen.findByText(/cancel_requested/)
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(7))
})

it('shows a truthful empty state and disables dispatch without input', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ peers: [], items: [] })))
  render(<RemoteMedia />)
  expect(await screen.findByText('No remote executions recorded.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Dispatch remote job' })).toBeDisabled()
})
