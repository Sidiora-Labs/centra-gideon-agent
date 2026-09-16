import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { KnowledgeEgoGraph, type KnowledgeGraphPayload } from './KnowledgeEgoGraph'


const FIXTURE: KnowledgeGraphPayload = {
  nodes: [
    { id: 'n-ada', name: 'Ada Lovelace', type: 'person' },
    { id: 'n-engine', name: 'Analytical Engine', type: 'project' },
    { id: 'n-babbage', name: 'Charles Babbage', type: 'person' },
    { id: 'n-zebra', name: 'Zebra Field Notes', type: 'topic' },
  ],
  edges: [
    { source: 'n-ada', target: 'n-engine', type: 'mentions', weight: 0.9 },
    { source: 'n-engine', target: 'n-babbage', type: 'similar_to', weight: 0.7 },
  ],
}

const shown = () => document.body.textContent ?? ''

const mount = (props: Partial<Parameters<typeof KnowledgeEgoGraph>[0]> = {}) =>
  render(<KnowledgeEgoGraph data={FIXTURE} focusId="n-ada" boxHeight={400} {...props} />)

const chips = (text: string) => screen.queryAllByText(text).filter((el) => el.tagName !== 'OPTION')

describe('the ego view renders the focused node and its 1-hop neighbourhood', () => {
  it('shows the focus and its direct neighbour, and nothing further out', () => {
    mount()
    expect(shown(), 'the focused document').toContain('Ada Lovelace')
    expect(shown(), 'its 1-hop neighbour').toContain('Analytical Engine')
    expect(shown(), 'two hops out, so not yet in range').not.toContain('Charles Babbage')
    expect(shown(), 'connected to nothing, so never in range').not.toContain('Zebra Field Notes')
  })

  it('distinguishes "never linked" from "linked, but out of range"', () => {
    const bare = mount({ data: { nodes: FIXTURE.nodes, edges: [] } })
    expect(shown(), 'no links at all: nothing to widen').toContain('No links yet')
    expect(shown()).not.toContain('widen the hops')
    bare.unmount()
    mount()
    expect(shown()).not.toContain('No links yet')
    const filter = screen.getByRole('combobox', { name: 'Filter links by type' })
    fireEvent.change(filter, { target: { value: 'similar_to' } })
    expect(shown(), 'links exist, none in range').toContain('Nothing links here within 1 hop')
    expect(shown()).toContain('widen the hops')
  })

  it('reports a focus that is not in the graph instead of an empty picture', () => {
    const missing = mount({ focusId: 'n-missing' })
    expect(shown()).toContain('This document is not in the graph')
    missing.unmount()
    mount()
    expect(shown()).not.toContain('This document is not in the graph')
  })
})

describe('raising the hop depth brings a 2-hop document into range, lowering it removes it', () => {
  it('expands and contracts around a named node', () => {
    mount()
    expect(shown(), 'depth 1: two hops out').not.toContain('Charles Babbage')

    fireEvent.click(screen.getByRole('tab', { name: '2' }))
    expect(shown(), 'depth 2: now in range').toContain('Charles Babbage')
    expect(shown(), 'the 1-hop neighbour is still drawn').toContain('Analytical Engine')
    expect(shown(), 'unconnected at any depth').not.toContain('Zebra Field Notes')

    fireEvent.click(screen.getByRole('tab', { name: '1' }))
    expect(shown(), 'back to depth 1: out of range again').not.toContain('Charles Babbage')
    expect(shown(), 'and the 1-hop neighbour survived the round trip').toContain('Analytical Engine')
  })

  it('offers 1..3 as an exclusive choice, named with the Studio canvas own words', () => {
    mount()
    expect(screen.getByRole('tablist', { name: 'Focus · hops' })).toBeTruthy()
    expect(shown(), 'the visible label matches the accessible name').toContain('Focus · hops')
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['1', '2', '3'])
    expect(screen.getByRole('tab', { name: '1' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: '2' }).getAttribute('aria-selected')).toBe('false')
    fireEvent.click(screen.getByRole('tab', { name: '3' }))
    expect(screen.getByRole('tab', { name: '3' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: '1' }).getAttribute('aria-selected')).toBe('false')
  })

  it('honours a starting depth so a rail can open pre-expanded', () => {
    const pre = mount({ defaultHopDepth: 2 })
    expect(shown()).toContain('Charles Babbage')
    pre.unmount()
    mount()
    expect(shown()).not.toContain('Charles Babbage')
  })
})

describe('the shared zoom control is on this canvas too — inherited, not forked', () => {
  it('mounts all three controls', () => {
    mount()
    expect(screen.getByRole('button', { name: 'Zoom in' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Zoom out' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Reset view' })).toBeTruthy()
  })

  it('comes from MemoryGraph, so there is exactly one copy of it', () => {
    const ego = readFileSync(join(process.cwd(), "src/features/knowledge/KnowledgeEgoGraph.tsx"), 'utf8')
    const canvas = readFileSync(join(process.cwd(), "src/features/settings/MemoryGraph.tsx"), 'utf8')
    expect(canvas, 'MemoryGraph mounts the shared control').toContain('<GraphZoomControls')
    expect(canvas, 'and imports it from ui/').toMatch(/import \{ GraphZoomControls \}/)
    expect(ego, 'the ego view must not import a second copy').not.toMatch(/import \{ GraphZoomControls/)
    expect(ego, 'nor render one').not.toMatch(/<GraphZoomControls/)
    expect(ego, 'because it delegates the whole canvas').toMatch(/import \{ MemoryGraph \}/)
  })
})

describe('the filter and the drawer speak the Memory Studio vocabulary, not a second one', () => {
  it('filters links by type with the same control name and the same any-sentinel', () => {
    mount()
    const filter = screen.getByRole('combobox', { name: 'Filter links by type' }) as HTMLSelectElement
    expect([...filter.options].map((o) => o.textContent)).toEqual([
      'Any link type',
      'mentions',
      'similar to',
    ])

    fireEvent.change(filter, { target: { value: 'similar_to' } })
    expect(shown(), 'the mentions edge was filtered out').not.toContain('Analytical Engine')
    expect(shown()).toContain('Nothing links here within 1 hop')

    fireEvent.change(filter, { target: { value: '' } })
    expect(shown()).toContain('Analytical Engine')
    expect(shown()).not.toContain('Nothing links here within 1 hop')
  })

  it('heads the drawer with "What links here" and chips the relation type', () => {
    mount()
    expect(shown()).toContain('What links here')
    expect(chips('mentions').length, 'the in-range relation is chipped').toBeGreaterThan(0)
    expect(chips('similar to').length, 'its edge is two hops out').toBe(0)
    fireEvent.click(screen.getByRole('tab', { name: '2' }))
    expect(chips('similar to').length).toBeGreaterThan(0)
  })

  it('escapes to the full graph with the Studio show-all link, and omits it when unwired', () => {
    const bare = mount()
    expect(screen.queryByRole('button', { name: /show all/i }), 'no dead link').toBeNull()
    bare.unmount()
    const onShowAll = vi.fn()
    mount({ onShowAll })
    fireEvent.click(screen.getByRole('button', { name: /show all/i }))
    expect(onShowAll).toHaveBeenCalledTimes(1)
  })
})

describe('clicking a neighbour hands its id up rather than navigating itself', () => {
  it('calls onSelect with the node id', () => {
    const onSelect = vi.fn()
    mount({ onSelect })
    const titles = [...document.querySelectorAll('title')]
    expect(titles.map((t) => t.textContent), 'the neighbour is on the canvas')
      .toContain('Analytical Engine (project)')
    const node = titles.find((t) => t.textContent === 'Analytical Engine (project)')!.closest('g')!
    fireEvent.click(node)
    expect(onSelect).toHaveBeenCalledWith('n-engine')
  })
})
