import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SUMMARY = {
  window_days: 7,
  total: 4,
  new_skills: { count: 2, names: ['auto/release-flow', 'auto/triage'] },
  refined_skills: { count: 1, names: ['auto/deploy'] },
  pending_proposals: { count: 1, names: ['auto/deploy (refine)'] },
  facts: { count: 0, names: [] },
}

const SKILLS = [
  { key: 'auto/release-flow', name: 'auto/release-flow', description: 'cut a release', source: 'auto', path: '/x', dir: '/x', always: false, status: 'active', loaded_by_agents: [] },
]

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      skills: () => Promise.resolve(SKILLS),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      learningSummary: () => Promise.resolve(SUMMARY),
      ...over,
    },
  }))
}

async function mountSkillsPage() {
  const { SkillsPage } = await import('./SkillsPage')
  render(<SkillsPage query={{}} setQuery={() => {}} />)
  await waitFor(() => expect(screen.getByText('cut a release')).toBeInTheDocument())
}

const region = () => screen.queryByRole('region', { name: /Learned in the last 7 days/i })

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('#/skills renders the learning summary block with real counts and names', () => {
  it('mounts the block on the skills page with each group\'s count and names', async () => {
    mockApi({})
    await mountSkillsPage()

    const block = await waitFor(() => {
      const r = region()
      expect(r, 'the block must MOUNT on the page, not merely exist as a component').not.toBeNull()
      return r!
    })
    const text = block.textContent ?? ''
    expect(text).toMatch(/2 new/)
    expect(text).toMatch(/auto\/release-flow/)
    expect(text).toMatch(/auto\/triage/)
    expect(text).toMatch(/1 refined/)
    expect(text).toMatch(/auto\/deploy/)
    expect(text).toMatch(/1 pending/)
    expect(text).not.toMatch(/facts/)
  })

  it('shows the EXACT count with a "+N more" remainder, never names.length', async () => {
    const capped = {
      ...SUMMARY,
      total: 12,
      new_skills: { count: 12, names: ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'] },
      refined_skills: { count: 0, names: [] },
      pending_proposals: { count: 0, names: [] },
    }
    mockApi({ learningSummary: () => Promise.resolve(capped) })
    await mountSkillsPage()

    const text = await waitFor(() => {
      const r = region()
      expect(r).not.toBeNull()
      return r!.textContent ?? ''
    })
    expect(text).toMatch(/12 new/)
    expect(text).toMatch(/\+4 more/)
    expect(text, 'the truncated sample size must never be presented as the count').not.toMatch(/8 new/)
  })

  it('is ABSENT when nothing was learned in the window, and the list still renders', async () => {
    mockApi({
      learningSummary: () => Promise.resolve({
        window_days: 7, total: 0,
        new_skills: { count: 0, names: [] }, refined_skills: { count: 0, names: [] },
        pending_proposals: { count: 0, names: [] }, facts: { count: 0, names: [] },
      }),
    })
    await mountSkillsPage()

    expect(region(), 'four zeros are noise, not information').toBeNull()
    expect(screen.getByText('cut a release'), 'the page itself is unaffected').toBeInTheDocument()
  })

  it('is ABSENT when the route 404s (learning disabled), and the list still renders', async () => {
    mockApi({ learningSummary: () => Promise.reject(new Error('404 learning is disabled')) })
    await mountSkillsPage()

    expect(region()).toBeNull()
    expect(screen.getByText('cut a release')).toBeInTheDocument()
    expect(screen.queryByRole('alert'), 'and not an error banner over a supplementary block').toBeNull()
  })
})

describe('the call site is on the page, not only in the component', () => {
  it('SkillsPage renders <LearningSummaryBlock /> inside the content column', () => {
    const src = readFileSync(join(__dirname, "SkillsPage.tsx"), 'utf8')
    expect(src).toContain("import { LearningSummaryBlock } from './LearningSummaryBlock'")
    expect(src).toContain('<LearningSummaryBlock />')
  })
})
