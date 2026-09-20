import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { InstalledPacks, PackStoreSection } from './PacksPanel'

vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))

describe('empty installed packs', () => {
  it('points to the Pack store section on the same page', () => {
    const { container } = render(
      <>
        <PackStoreSection installed={[]} onInstalled={vi.fn()} />
        <InstalledPacks packs={[]} />
      </>,
    )

    expect(screen.getByText('No packs installed yet.', { exact: false })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Pack store above' }).getAttribute('href')).toBe('#pack-store')
    expect(container.querySelector('#pack-store')).toBeTruthy()
  })
})
