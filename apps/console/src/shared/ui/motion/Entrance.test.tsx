
import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

import { EntranceGroup, EntranceRegion } from './Entrance'

function Surface({ label = 'first', onPoke = () => {} }: { label?: string; onPoke?: () => void }) {
  return (
    <EntranceGroup className="flex flex-col">
      <EntranceRegion><p>{label} region</p></EntranceRegion>
      <EntranceRegion>
        <button type="button" onClick={onPoke}>poke</button>
      </EntranceRegion>
      <EntranceRegion><p>third region</p></EntranceRegion>
    </EntranceGroup>
  )
}

const group = () => document.querySelector<HTMLElement>('[data-entrance]')!
const regions = () => [...document.querySelectorAll<HTMLElement>('[data-entrance-region]')]

describe('EntranceGroup / EntranceRegion — the cascade is wired', () => {
  it('the group declares the staggered branch and every region joins it', () => {
    render(<Surface />)
    expect(group()).toHaveAttribute('data-entrance', 'staggered')
    expect(regions()).toHaveLength(3)
    for (const r of regions()) expect(r).toHaveAttribute('data-entrance-region', 'staggered')
  })

  it('the variant actually reaches the regions — they start hidden and land visible', async () => {
    render(<Surface />)
    expect(regions().map((r) => r.style.opacity)).toEqual(['0', '0', '0'])
    await waitFor(() => expect(regions().every((r) => r.style.opacity === '1')).toBe(true))
  })
})

describe('an entrance never gates content', () => {
  it('every region is in the document on the FIRST commit, before anything animates', () => {
    render(<Surface />)
    expect(screen.getByText('first region')).toBeInTheDocument()
    expect(screen.getByText('third region')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'poke' })).toBeInTheDocument()
  })

  it('a control inside a mid-cascade region is already usable', () => {
    const onPoke = vi.fn()
    render(<Surface onPoke={onPoke} />)
    screen.getByRole('button', { name: 'poke' }).click()
    expect(onPoke).toHaveBeenCalledTimes(1)
  })
})

describe('the replay rule — mount plays it, a re-render never does', () => {
  it('a data change re-renders the regions in place instead of remounting them', async () => {
    const { rerender } = render(<Surface label="first" />)
    await waitFor(() => expect(regions().every((r) => r.style.opacity === '1')).toBe(true))
    const before = { g: group(), r: regions() }

    rerender(<Surface label="second" />)
    expect(screen.getByText('second region')).toBeInTheDocument()

    expect(group()).toBe(before.g)
    expect(regions()).toEqual(before.r)
    expect(regions().map((r) => r.style.opacity)).toEqual(['1', '1', '1'])
  })
})

describe('a region without a group', () => {
  it('renders plain and visible — a forgotten group costs the entrance, never the content', () => {
    render(<EntranceRegion><p>orphan</p></EntranceRegion>)
    const r = regions()[0]
    expect(r).toHaveAttribute('data-entrance-region', 'none')
    expect(r.style.opacity).toBe('')
    expect(screen.getByText('orphan')).toBeInTheDocument()
  })
})
