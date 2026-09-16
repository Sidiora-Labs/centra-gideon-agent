import { describe, it, expect } from 'vitest'
import { WIDTH_PRESETS, DEFAULT_WIDTH_PRESET } from './appearance'
import { appearanceReducer, defaultAppearance, parseAppearance } from './appearanceState'

describe('content width preferences', () => {
  it('starts at full width while preserving the named 1100px preset', () => {
    expect(DEFAULT_WIDTH_PRESET).toBe('full')
    expect(WIDTH_PRESETS[DEFAULT_WIDTH_PRESET]).toBe('100%')
    expect(WIDTH_PRESETS.default).toBe('1100px')
    expect(Object.keys(WIDTH_PRESETS).sort()).toEqual(['default', 'full', 'narrow', 'wide'])
  })
  it.each([null, '{}', '{"colors":{},"scalars":{}}', '{"widthPreset":"unknown"}', 'null', '{'])('normalizes old or invalid persisted data: %s', (raw) => {
    expect(parseAppearance(raw).widthPreset).toBe(DEFAULT_WIDTH_PRESET)
  })
  it('restores every explicit width and resets through the factory default', () => {
    for (const widthPreset of Object.keys(WIDTH_PRESETS)) {
      const loaded = parseAppearance(JSON.stringify({ widthPreset }))
      expect(loaded.widthPreset).toBe(widthPreset)
      expect(appearanceReducer(loaded, { type: 'reset' }).widthPreset).toBe(DEFAULT_WIDTH_PRESET)
    }
  })
  it('creates independent override maps for each profile', () => {
    const first = defaultAppearance()
    first.scalars['--font-scale'] = 150
    expect(defaultAppearance().scalars).toEqual({})
  })
})
