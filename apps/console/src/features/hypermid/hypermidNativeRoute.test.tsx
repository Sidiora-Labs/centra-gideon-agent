import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { activeNavigationId, navigationItems, ROUTABLE_ROOTS } from '../../app/shell/navigationModel'
import { HypermidPage } from './HypermidPage'

describe('Hypermid native destination', () => {
  it('is an OSS route with a human-readable navigation entry', () => {
    const item = navigationItems(false).find(({ id }) => id === 'hypermid')
    expect(item?.label).toBe('Hypermid')
    expect(item?.section).toBe('More')
    expect(navigationItems(true).some(({ id }) => id === 'hypermid')).toBe(false)
    expect(ROUTABLE_ROOTS.has('hypermid')).toBe(true)
    expect(activeNavigationId('hypermid', '', {})).toBe('hypermid')
  })

  it('renders inside the native page shell with an accessible administration region', () => {
    const html = renderToStaticMarkup(<HypermidPage
      sub=""
      navigate={vi.fn()}
      navEpoch={0}
      query={{}}
      setQuery={vi.fn()}
    />)
    expect(html).toContain('Hypermid administration')
    expect(html).toContain('Hypermid status')
    expect(html).toContain('role="tablist"')
  })
})
