import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import Page from './Page'

afterEach(() => { cleanup(); location.hash = ''; vi.restoreAllMocks() })

it('mounts manuscript exports from the creative workspace route', async () => {
  location.hash = '#/capabilities/creative?view=exports'
  const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ items: [] })))
  render(<Page apiRoot="/api/capabilities/creative/ingredients" />)
  expect(await screen.findByRole('heading', { name: 'Manuscript exports' })).toBeInTheDocument()
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/capabilities/creative/exports', expect.anything()))
})

it('loads the selected series revision into production with the exact series root', async () => {
  location.hash = '#/capabilities/creative?view=production&series=series-1'
  const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = String(input)
    return new Response(JSON.stringify(url.endsWith('/production') ? { items: [] } : { id: 'series-1', revision: 7 }))
  })
  render(<Page apiRoot="/api/capabilities/creative/ingredients" />)
  expect(await screen.findByRole('heading', { name: 'Bounded series production' })).toBeInTheDocument()
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/capabilities/creative/series/series-1/production', expect.anything()))
})

it('mounts creative direction with the exact shared route', async () => {
  location.hash = '#/capabilities/creative?view=direction'
  const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ items: [] })))
  render(<Page apiRoot="/api/capabilities/creative/ingredients" />)
  expect(await screen.findByRole('heading', { name: 'Creative direction' })).toBeInTheDocument()
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/capabilities/creative/direction', expect.anything()))
})

it('mounts recurring commissions with the exact shared route', async () => {
  location.hash = '#/capabilities/creative?view=commissions'
  const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ items: [] })))
  render(<Page apiRoot="/api/capabilities/creative/ingredients" />)
  expect(await screen.findByRole('heading', { name: 'Commissions' })).toBeInTheDocument()
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/capabilities/creative/commissions'))
})
