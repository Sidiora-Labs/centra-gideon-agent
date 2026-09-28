import { describe, expect, it } from 'vitest'
import { appUiAssetUrl } from './AppHostPage'

describe('versioned app UI delivery', () => {
  it('changes the served module URL when the installed UI revision changes', () => {
    const first = appUiAssetUrl('switchboard-app', 'page.js', 'revision-one')
    const updated = appUiAssetUrl('switchboard-app', 'page.js', 'revision-two')

    expect(first).toBe('/apps/switchboard-app/ui/page.js?v=revision-one')
    expect(updated).toBe('/apps/switchboard-app/ui/page.js?v=revision-two')
    expect(first).not.toBe(updated)
  })
})
