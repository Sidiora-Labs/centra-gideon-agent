import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { FacetControls } from './MemoryPanel'

describe('persisted preference controls', () => {
  it('renders pin, unpin and forgotten states from persisted flags', () => {
    const render = (pinned: boolean, forgotten: boolean) => renderToStaticMarkup(
      <FacetControls fact={{ key: 'pref.facet.style.short', value_json: JSON.stringify({ pinned, forgotten }) }} onSaved={() => {}} />,
    )
    expect(render(false, false)).toContain('Pin preference')
    expect(render(true, false)).toContain('Unpin preference')
    expect(render(false, false)).toContain('Forget preference')
    expect(render(true, true)).toContain('Preference forgotten')
    expect(render(true, true).match(/disabled=""/g)).toHaveLength(2)
  })
})
