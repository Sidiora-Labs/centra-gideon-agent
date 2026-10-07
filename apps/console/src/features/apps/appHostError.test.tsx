import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { AppHostPage } from './AppHostPage'
import { api, ApiError } from '../../shared/data/api'


describe('AppHostPage error split', () => {
  it('404 → "isn\'t installed" EmptyState with a Store action, no alert', async () => {
    vi.spyOn(api, 'app').mockRejectedValueOnce(new ApiError("app 'ghost' not installed", 404))
    const navigate = vi.fn()
    render(<AppHostPage sub="ghost" navigate={navigate} />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'App no longer installed' })).toBeTruthy())
    expect(screen.getByText('Install it from the Store to open it here.')).toBeTruthy()
    expect(screen.getByRole('button', { name: /open the store/i })).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    screen.getByRole('button', { name: /open the store/i }).click()
    expect(navigate).toHaveBeenCalledWith('apps?view=store')
  })

  it('the Store action routes to the store view of the Apps page', async () => {
    vi.spyOn(api, 'app').mockRejectedValueOnce(new ApiError("app 'phantom' not installed", 404))
    const navigate = vi.fn()
    render(<AppHostPage sub="phantom" navigate={navigate} />)
    await waitFor(() => expect(screen.getByRole('button', { name: /open the store/i })).toBeTruthy())
    screen.getByRole('button', { name: /open the store/i }).click()
    expect(navigate).toHaveBeenCalledWith('apps?view=store')
  })

  it('a non-404 failure → the retryable LoadError alert, and Retry re-fetches', async () => {
    const spy = vi.spyOn(api, 'app').mockRejectedValueOnce(new ApiError('boom', 500))
    render(<AppHostPage sub="flaky" navigate={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    expect(screen.getByText(/couldn't load your app/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /open the store/i })).toBeNull()
    const calls = spy.mock.calls.length
    screen.getByRole('button', { name: /retry/i }).click()
    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(calls))
  })
})
