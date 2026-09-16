import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SourceCreatePage } from './SourceCreatePage'
import { api, type SourceKind, type SourceRecipe } from '../../shared/data/api'


const WEB_KIND: SourceKind = {
  provider: 'watched-page', display_name: 'Watched Page', kind: 'web_page', form: 'web_page',
  previewable: true, poll_interval_secs: 3600, default_item_type: 'bookmark',
  detectors: ['json_ld', 'semantic_html'],
}
const FEED_KIND: SourceKind = {
  provider: 'watched-feed', display_name: 'Watched Feed', kind: 'feed', form: 'feed',
  previewable: false, poll_interval_secs: 1800, default_item_type: 'bookmark',
  formats: ['csv', 'json', 'rss'], presets: ['atom', 'hn_algolia', 'rss'],
}

function recipe(over: Partial<SourceRecipe> = {}): SourceRecipe {
  return {
    id: 'github-releases', displayName: 'GitHub releases',
    description: 'Every published release of one repository.',
    provider: 'watched-feed', kind: 'feed', itemType: 'bookmark', enrichment: '',
    spec: { preset: 'atom', url: 'https://github.com/astral-sh/uv/releases.atom' },
    groups: { owner: 'astral-sh', repo: 'uv' },
    ...over,
  }
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api, 'knowledgeSources').mockResolvedValue({
    sources: [], kinds: [WEB_KIND, FEED_KIND],
    health_statuses: ['ok', 'degraded', 'error', 'needs render tier'], raw_enrichment: 'raw',
  })
})

async function lookUp(url: string) {
  const user = userEvent.setup()
  render(<SourceCreatePage onBack={() => {}} onCreated={() => {}} />)
  const box = await screen.findByLabelText('A URL to look up in the recipe directory')
  await user.type(box, url)
  await user.click(screen.getByRole('button', { name: /Check/ }))
  return user
}

describe('the recipe lookup answers "is this site already covered?"', () => {
  it('offers a matching recipe and names the URL it will actually watch', async () => {
    vi.spyOn(api, 'knowledgeSourceRecipes').mockResolvedValue({
      recipes: [recipe()], matches: [recipe()], url: 'https://github.com/astral-sh/uv',
    })

    await lookUp('https://github.com/astral-sh/uv')

    expect(await screen.findByText('GitHub releases')).toBeTruthy()
    expect(screen.getByText('https://github.com/astral-sh/uv/releases.atom')).toBeTruthy()
  })

  it('seeds the form with the resolved spec when the recipe is used', async () => {
    vi.spyOn(api, 'knowledgeSourceRecipes').mockResolvedValue({
      recipes: [recipe()], matches: [recipe()], url: 'https://github.com/astral-sh/uv',
    })

    const user = await lookUp('https://github.com/astral-sh/uv')
    await user.click(await screen.findByRole('button', { name: 'Use the GitHub releases recipe' }))

    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Name' })).toBeTruthy())
    expect((screen.getByRole('textbox', { name: 'Name' }) as HTMLInputElement).value)
      .toBe('GitHub releases')
    const url = screen.getByRole('textbox', { name: /Feed URL|URL/ }) as HTMLInputElement
    expect(url.value).toBe('https://github.com/astral-sh/uv/releases.atom')
  })

  it('says plainly when nothing covers the URL', async () => {
    vi.spyOn(api, 'knowledgeSourceRecipes').mockResolvedValue({
      recipes: [recipe()], matches: [], url: 'https://example.com/',
    })

    await lookUp('https://example.com/')

    expect(await screen.findByText(/No recipe covers that URL yet/)).toBeTruthy()
    expect(screen.queryByText('GitHub releases')).toBeNull()
  })

  it('hides a recipe whose provider this install has not registered', async () => {
    vi.spyOn(api, 'knowledgeSourceRecipes').mockResolvedValue({
      recipes: [], matches: [recipe({ id: 'ghost', displayName: 'Ghost kind', provider: 'not-registered' })],
      url: 'https://ghost.example/',
    })

    await lookUp('https://ghost.example/')

    expect(await screen.findByText(/No recipe covers that URL yet/)).toBeTruthy()
    expect(screen.queryByText('Ghost kind')).toBeNull()
  })

  it('surfaces a lookup failure instead of reading as "not covered"', async () => {
    vi.spyOn(api, 'knowledgeSourceRecipes').mockRejectedValue(new Error('offline'))

    await lookUp('https://github.com/astral-sh/uv')

    expect(await screen.findByRole('alert')).toHaveTextContent('offline')
    expect(screen.queryByText(/No recipe covers that URL yet/)).toBeNull()
  })
})
