import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { SearchCapabilitiesInfo } from '../../shared/data/api'
import { SearchPanel } from './SearchPanel'


const searchProviders = vi.fn()
const searchActive = vi.fn()
const setActiveSearchProvider = vi.fn()
const tools = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    searchProviders: (...a: unknown[]) => searchProviders(...a),
    searchActive: (...a: unknown[]) => searchActive(...a),
    setActiveSearchProvider: (...a: unknown[]) => setActiveSearchProvider(...a),
    tools: (...a: unknown[]) => tools(...a),
  },
}))

const CAPS: SearchCapabilitiesInfo = {
  returns_content: true, returns_answer: true, returns_highlights: false,
  supports_recency: true, supports_domains: false, supports_fetch: false, depths: [],
}

async function openRow(name: RegExp) {
  fireEvent.click(await screen.findByRole('button', { name }))
}

describe('the search bind list announces which provider is bound, and to what', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    searchProviders.mockResolvedValue([
      { name: 'searxng', display_name: 'SearXNG', capabilities: CAPS, available: true },
      { name: 'tavily', display_name: 'Tavily', capabilities: { ...CAPS, supports_fetch: true }, available: false },
    ])
    searchActive.mockResolvedValue({ 'search-general': ['searxng'] })
    tools.mockResolvedValue([{ name: 'web_search', disabled: false, providerDisabled: false }])
  })

  it('the group is named with its use case, so four sibling lists are distinguishable', async () => {
    render(<SearchPanel />)
    await openRow(/General search/)
    expect(await screen.findByRole('group', { name: 'General search provider' })).toBeTruthy()
    await openRow(/News search/)
    expect(screen.getByRole('group', { name: 'News search provider' })).toBeTruthy()
  })

  it('exactly one option is pressed, and it is the bound one', async () => {
    render(<SearchPanel />)
    await openRow(/General search/)
    const group = await screen.findByRole('group', { name: 'General search provider' })
    const opts = [...group.querySelectorAll('button')]
    expect(opts.map((b) => b.textContent?.includes('SearXNG'))).toContain(true)
    const pressed = opts.filter((b) => b.getAttribute('aria-pressed') === 'true')
    expect(pressed.length, 'single-select: one pressed, not zero and not two').toBe(1)
    expect(pressed[0].textContent, 'and it is the provider the backend reports as bound').toContain('SearXNG')
    const unpressed = opts.filter((b) => b.getAttribute('aria-pressed') === 'false')
    expect(unpressed.length).toBe(1)
    expect(unpressed[0].textContent).toContain('Tavily')
  })

  it('an unbound use case presses nothing at all', async () => {
    render(<SearchPanel />)
    await openRow(/Financial search/)
    const group = await screen.findByRole('group', { name: 'Financial search provider' })
    const opts = [...group.querySelectorAll('button')]
    expect(opts.length, 'both providers are eligible for a plain search use case').toBe(2)
    expect(opts.filter((b) => b.getAttribute('aria-pressed') === 'true')).toEqual([])
  })

  it('the state follows the write, not the click', async () => {
    render(<SearchPanel />)
    await openRow(/General search/)
    const group = await screen.findByRole('group', { name: 'General search provider' })
    searchActive.mockResolvedValue({ 'search-general': ['tavily'] })
    setActiveSearchProvider.mockResolvedValue({ ok: true })
    fireEvent.click([...group.querySelectorAll('button')].find((b) => b.textContent?.includes('Tavily'))!)
    expect(setActiveSearchProvider).toHaveBeenCalledWith('search-general', ['tavily'])
    await waitFor(() => {
      const now = [...screen.getByRole('group', { name: 'General search provider' }).querySelectorAll('button')]
      expect(now.find((b) => b.getAttribute('aria-pressed') === 'true')?.textContent).toContain('Tavily')
    })
  })

  it('fetch-article only offers a provider that can extract content', async () => {
    render(<SearchPanel />)
    await openRow(/Article fetch/)
    const group = await screen.findByRole('group', { name: 'Article fetch provider' })
    const opts = [...group.querySelectorAll('button')]
    expect(opts.length).toBe(1)
    expect(opts[0].textContent).toContain('Tavily')
  })

  it('distinguishes provider setup from the web_search tool prerequisite with deep links', async () => {
    searchProviders.mockResolvedValue([])
    tools.mockResolvedValue([{ name: 'web_search', disabled: true, providerDisabled: false }])
    render(<SearchPanel />)

    const providers = await screen.findByRole('link', { name: 'Providers' })
    expect(providers.getAttribute('href')).toBe('#/settings/providers')
    expect(providers.className).toContain('underline')
    const tool = screen.getByRole('link', { name: 'Tools' })
    expect(tool.getAttribute('href')).toBe('#/tools')
    expect(tool.className).toContain('underline')
  })

  it('does not call a failed tools read an unavailable web_search tool', async () => {
    tools.mockRejectedValue(new Error('offline'))
    render(<SearchPanel />)

    await screen.findByText('General search')
    expect(screen.queryByText(/web_search tool is unavailable/)).toBeNull()
  })
})
