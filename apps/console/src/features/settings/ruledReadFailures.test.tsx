import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import ts from 'typescript'
import { swallowedReads } from '../../shared/testing/swallowCensus'
import { invalidateKeys } from '../../shared/data/data'
import { SearchPanel } from './SearchPanel'
import { FeedbackPanel } from './FeedbackPanel'
import { DailyDigestSection } from './MemoryPanel'
import { MultiInstanceCard } from './MultiInstanceCard'
import { PacksPanel, PackStoreSection } from './PacksPanel'
import { UsagePanel } from './UsagePanel'
import { ProposalActions } from '../inbox/InboxDetail'

const source = (name: string) => readFileSync(join(process.cwd(), 'src/features', name), 'utf8')
const refreshPacks = () => invalidateKeys('settings:packs:installed')
const refreshProviders = () => invalidateKeys('settings:provider-instances', true)
const refreshProposals = () => invalidateKeys('skill-proposals', true)
const navigate = (path: string) => { window.location.hash = path }
const setQuery = (patch: Record<string, string | null | undefined>) => {
  const params = new URLSearchParams(window.location.search)
  for (const [key, value] of Object.entries(patch)) {
    if (value == null) params.delete(key)
    else params.set(key, value)
  }
  window.history.replaceState(null, '', `?${params}`)
}

// The real Node fetch rejects relative browser gateway URLs; no API or hook is replaced.
describe('ruled read failures', () => {
  const cases = [
    ['search settings', <SearchPanel />, /No search providers configured/],
    ['feedback sources', <FeedbackPanel />, /No feedback yet/],
    ['daily digests', <DailyDigestSection />, /No daily digests yet/],
    ['provider instances', <MultiInstanceCard ext={{ name: 'unavailable', enabled: true }} onChanged={refreshProviders} />, /No instances yet/],
    ['settings', <PacksPanel />, /No packs installed yet/],
    ['pack catalog', <PackStoreSection installed={[]} onInstalled={refreshPacks} />, /Installed/],
    ['usage', <UsagePanel query={{}} setQuery={setQuery} />, /No model usage recorded/],
    ['skill proposal', <ProposalActions pid="unavailable" onChanged={refreshProposals} navigate={navigate} />, /This proposal was already answered/],
  ] as const

  for (const [what, element, falseEmpty] of cases) {
    it(`${what} exposes an actual transport rejection and retries without claiming empty data`, async () => {
      render(element)
      expect(await screen.findByRole('heading', { name: `Couldn't load your ${what}` })).toBeInTheDocument()
      expect(screen.queryByText(falseEmpty)).not.toBeInTheDocument()
      expect(screen.getByRole('alert').textContent).toContain('Failed to parse URL')
      fireEvent.click(screen.getByRole('button', { name: /retry/i }))
      await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Failed to parse URL'))
      expect(screen.queryByText(falseEmpty)).not.toBeInTheDocument()
    })
  }

  const branches: Array<[string, string, string, string]> = [
    ['settings/SearchPanel.tsx', 'export function SearchPanel', 'if (loadErr)', 'if (!providers)'],
    ['settings/FeedbackPanel.tsx', 'export function FeedbackPanel', '{loadErr ? <LoadError', ': !data ? <ListSkeleton'],
    ['settings/MemoryPanel.tsx', 'export function DailyDigestSection', '{loadErr ? <LoadError', ': !digests ? <ListSkeleton'],
    ['settings/MultiInstanceCard.tsx', 'export function MultiInstanceCard', '{loadErr ? <LoadError', ': instances === undefined || schema === undefined'],
    ['settings/PacksPanel.tsx', 'export function PacksPanel', '{installedErr ? <LoadError', ': installed === undefined ? <FormSkeleton'],
    ['settings/PacksPanel.tsx', 'export function PackStoreSection', '{error ? <LoadError', ': bundled === undefined ? <FormSkeleton'],
    ['settings/UsagePanel.tsx', 'export function UsagePanel', 'if (loadErr)', 'if ([totals, byModel'],
    ['inbox/InboxDetail.tsx', 'export function ProposalActions', 'if (loadErr)', 'if (gone)'],
  ]
  for (const [file, boundary, errorBranch, loadingBranch] of branches) {
    it(`${boundary} reaches the error before loading or empty`, () => {
      const code = source(file)
      const body = code.slice(code.indexOf(boundary)).split(/\n(?:export )?function /)[0]
      expect(body.indexOf(errorBranch)).toBeGreaterThanOrEqual(0)
      expect(body.indexOf(loadingBranch)).toBeGreaterThan(body.indexOf(errorBranch))
      expect(swallowedReads(body)).toEqual([])
      const parsed = ts.createSourceFile(file, code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
      expect((parsed as typeof parsed & { parseDiagnostics: unknown[] }).parseDiagnostics).toEqual([])
    })
  }

  it('keeps unknown installed packs away from install controls and false empty lists', () => {
    const code = source('settings/PacksPanel.tsx')
    expect(code).not.toContain('installed ?? []')
    expect(code).toContain('!installedErr && installed !== undefined && <InstalledPacks')
    expect(code).not.toMatch(/api\.packs(?:Installed|Bundled)\(\)\.catch/)
  })

  it('only missing or gone proposals are presented as already answered', () => {
    const code = source('inbox/InboxDetail.tsx')
    expect(code).toContain('error instanceof ApiError && (error.status === 404 || error.status === 410)')
    expect(code).toContain('else setLoadErr(error)')
  })

  it('shared Search, Feedback and Packs readers cannot poison their panel caches', () => {
    const code = source('settings/settingsWidgets.tsx')
    for (const api of ['searchProviders', 'searchActive', 'feedbackProducers', 'packsInstalled']) {
      expect(code).not.toContain(`api.${api}().catch`)
    }
    expect(code).toContain('return { providers, active, tools }')
    expect(code).toContain('loading={data === undefined && !searchErr}')
    expect(code).toContain('loading={data === undefined && !feedbackErr}')
    expect(code).toContain('&& !packsErr && !installedErr}')
    for (const what of ['search settings', 'feedback sources', 'installed packs']) {
      expect(code).toContain(`Couldn&rsquo;t load ${what}.`)
    }
  })
})
