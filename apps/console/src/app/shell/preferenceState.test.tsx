import { afterEach, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { appearanceReducer, defaultAppearance, parseAppearance, snapshotColors } from './appearanceState'
import { identityReducer, initialIdentity } from './identityState'
import { ThemeProvider, themeReducer, resolveMode, useMode } from './theme'
import { DEFAULT_SCHEME, getScheme } from '../../shared/theme/schemes'
import { decodeVapidKey, pushDeviceId } from './pushClient'
import { nativeBridge } from './nativePush'

afterEach(() => localStorage.clear())
it('rejects damaged persisted fields while retaining valid independent preferences', () => {
  const loaded = parseAppearance(JSON.stringify({ colors: { '--a': { dark: '#abc', light: 4 }, '--b': null }, scalars: { '--size': 5, '--bad': '5' }, selects: { '--font': 'mono', '--bad': false }, widthPreset: 'wide' }))
  expect(loaded.colors).toEqual({ '--a': { dark: '#abc' } })
  expect(loaded.scalars).toEqual({ '--size': 5 })
  expect(loaded.selects).toEqual({ '--font': 'mono' })
  expect(loaded.widthPreset).toBe('wide')
})
it('edits and resets a token independently of its neighboring fields', () => {
  let state = appearanceReducer(defaultAppearance(), { type: 'color', key: '--color-primary', mode: 'dark', value: '#123456' })
  expect(state.scheme).toBe('custom:unsaved')
  state = appearanceReducer(state, { type: 'scalar', key: '--size', value: 7 })
  state = appearanceReducer(state, { type: 'reset', key: '--color-primary' })
  expect(state.colors).toEqual({})
  expect(state.scalars).toEqual({ '--size': 7 })
  expect(snapshotColors(state).dark['--color-primary']).not.toBe('#123456')
})
it('saved theme edits retain identity and removing only the active theme restores the default', () => {
  const scheme = { ...getScheme(DEFAULT_SCHEME)!, id: 'custom:owned' }
  let state = appearanceReducer(defaultAppearance(), { type: 'scheme', scheme })
  state = appearanceReducer(state, { type: 'color', key: '--color-primary', mode: 'light', value: '#321654' })
  expect(state.scheme).toBe(scheme.id)
  expect(appearanceReducer(state, { type: 'remove-scheme', id: 'custom:other' })).toBe(state)
  const restored = appearanceReducer(state, { type: 'remove-scheme', id: scheme.id })
  expect(restored.scheme).toBe(DEFAULT_SCHEME)
  expect(restored.colors).toEqual(getScheme(DEFAULT_SCHEME)?.colors)
})
it('a delayed identity load cannot erase a local edit, including clearing the name', () => {
  let state = identityReducer(initialIdentity, { type: 'edit', name: '  Ada  ' })
  state = identityReducer(state, { type: 'loaded', name: 'Old name', revision: 0 })
  expect(state).toMatchObject({ name: 'Ada', loaded: true })
  state = identityReducer(state, { type: 'edit', name: '' })
  expect(identityReducer(state, { type: 'loaded', name: 'Old name', revision: 0 }).name).toBe('')
})
it('auto follows the system until an explicit toggle selects the opposite mode', () => {
  let state = themeReducer({ preference: 'auto', system: 'dark' }, { type: 'system', value: 'light' })
  expect(resolveMode(state)).toBe('light')
  state = themeReducer(state, { type: 'toggle' })
  expect(state.preference).toBe('dark')
  expect(resolveMode(themeReducer(state, { type: 'system', value: 'light' }))).toBe('dark')
})
it('syncs a real storage event to the provider and document with stable preference commands', () => {
  localStorage.setItem('mode', 'dark')
  const { result } = renderHook(useMode, { wrapper: ThemeProvider })
  const setPreference = result.current.setPreference
  act(() => window.dispatchEvent(new StorageEvent('storage', { key: 'mode', newValue: 'light' })))
  expect(result.current.mode).toBe('light')
  expect(document.documentElement.dataset.mode).toBe('light')
  expect(document.documentElement.classList.contains('light')).toBe(true)
  expect(result.current.setPreference).toBe(setPreference)
  act(() => result.current.toggle())
  expect(localStorage.getItem('mode')).toBe('dark')
})
it('roundtrips VAPID bytes and keeps a profile device identity stable', () => {
  expect([...new Uint8Array(decodeVapidKey('AQID-_8'))]).toEqual([1, 2, 3, 251, 255])
  const identity = pushDeviceId()
  expect(identity).toMatch(/^web-/)
  expect(pushDeviceId()).toBe(identity)
  expect(localStorage.getItem('gideon:push:device_id')).toBe(identity)
  expect(nativeBridge(null)).toBeNull()
})

it('updates the inherited default while preserving selected palettes and other preferences', () => {
  const inherited = parseAppearance(JSON.stringify({ scheme: 'coral', colors: {}, widthPreset: 'wide', selects: { '--font-family': 'dm-sans' } }))
  expect(inherited.scheme).toBe(DEFAULT_SCHEME)
  expect(inherited.widthPreset).toBe('wide')
  expect(inherited.selects['--font-family']).toBe('dm-sans')
  const coral = getScheme('coral')!
  const selected = parseAppearance(JSON.stringify({ scheme: coral.id, colors: coral.colors }))
  expect(selected.scheme).toBe('coral')
  expect(selected.colors).toEqual(coral.colors)
})
