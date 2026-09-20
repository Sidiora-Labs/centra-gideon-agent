import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SourcesPanel } from './apps/AppsSection'

const read = (path: string) => readFileSync(join(process.cwd(), 'src/features', path), 'utf8')
const catalog = {
  bundled: [], gitSources: [], localSources: [], localApps: [], remoteApps: [], gitApps: [],
  networkSources: ['github.com'],
}

afterEach(cleanup)

describe('task 72 silent frontend surfaces', () => {
  it('withholds source emptiness and egress claims until the read settles', () => {
    const view = render(<SourcesPanel catalog={catalog} settled={false} reloadCatalog={() => {}} onInstalled={() => {}} />)
    expect(screen.queryByText(/No git sources configured/)).toBeNull()
    expect(screen.queryByTestId('store-egress-disclosure')).toBeNull()
    view.rerender(<SourcesPanel catalog={catalog} settled reloadCatalog={() => {}} onInstalled={() => {}} />)
    expect(screen.getByText(/No git sources configured/)).toBeTruthy()
    expect(screen.getByTestId('store-egress-disclosure')).toBeTruthy()
  })

  it('reports every explicit optimize-button outcome without making slash optimize noisy', () => {
    const chat = read('ChatPage.tsx')
    const explicit = chat.slice(chat.indexOf('async function optimize()'), chat.indexOf('async function optimizeAndSend'))
    const slash = chat.slice(chat.indexOf('async function optimizeAndSend'), chat.indexOf('function revertOptimize'))
    expect(explicit).toContain("no changes needed.', 'info'")
    expect(explicit).toContain("before optimizing this prompt.', 'error'")
    expect(explicit).toContain("Couldn't optimize this prompt:")
    expect(slash).not.toContain('notify(')
    expect(read('loop/LoopComposer.tsx')).toContain("no changes needed.', 'info'")
  })

  it('explains a fresh active intent and withholds the backfill hint while paused', () => {
    expect(read('knowledge/KnowledgeListPage.tsx')).toContain(
      "it.enabled ? 'nothing gathered yet — run on existing items' : 'nothing gathered yet'",
    )
  })
})
