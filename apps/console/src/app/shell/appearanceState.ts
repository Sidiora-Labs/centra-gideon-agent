import { TOKENS, type Token } from '../../shared/theme/tokenRegistry'
import { DEFAULT_SCHEME, getScheme, type Scheme } from '../../shared/theme/schemes'
import type { ThemeRecord } from '../../shared/data/api'

export type WidthPreset = 'narrow' | 'default' | 'wide' | 'full'
export const DEFAULT_WIDTH_PRESET: WidthPreset = 'full'
export const WIDTH_PRESETS: Record<WidthPreset, string> = { narrow: '768px', default: '1100px', wide: '1440px', full: '100%' }
export interface AppearanceState {
  colors: Record<string, { dark?: string; light?: string }>
  scalars: Record<string, number>
  selects: Record<string, string>
  scheme: string
  widthPreset: WidthPreset
}
export type AppearanceAction =
  | { type: 'color'; key: string; mode: 'dark' | 'light'; value: string }
  | { type: 'scalar'; key: string; value: number }
  | { type: 'select'; key: string; value: string }
  | { type: 'width'; value: WidthPreset }
  | { type: 'scheme'; scheme: Scheme }
  | { type: 'remove-scheme'; id: string }
  | { type: 'reset'; key?: string }

export function defaultAppearance(): AppearanceState {
  return { colors: {}, scalars: {}, selects: {}, scheme: DEFAULT_SCHEME, widthPreset: DEFAULT_WIDTH_PRESET }
}
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
export function parseAppearance(raw: string | null): AppearanceState {
  const result = defaultAppearance()
  if (!raw) return result
  try {
    const saved: unknown = JSON.parse(raw)
    if (!record(saved)) return result
    if (record(saved.colors)) for (const [key, modes] of Object.entries(saved.colors)) {
      if (!record(modes)) continue
      result.colors[key] = Object.fromEntries(Object.entries(modes).filter(([mode, value]) => (mode === 'dark' || mode === 'light') && typeof value === 'string'))
    }
    if (record(saved.scalars)) result.scalars = Object.fromEntries(Object.entries(saved.scalars).filter((entry): entry is [string, number] => typeof entry[1] === 'number' && Number.isFinite(entry[1])))
    if (record(saved.selects)) result.selects = Object.fromEntries(Object.entries(saved.selects).filter((entry): entry is [string, string] => typeof entry[1] === 'string'))
    if (typeof saved.scheme === 'string') result.scheme = saved.scheme
    if (result.scheme === 'coral' && Object.keys(result.colors).length === 0) result.scheme = DEFAULT_SCHEME
    if (typeof saved.widthPreset === 'string' && Object.hasOwn(WIDTH_PRESETS, saved.widthPreset)) result.widthPreset = saved.widthPreset as WidthPreset
  } catch { /* A damaged local preference leaves factory values in place. */ }
  return result
}

export function appearanceReducer(state: AppearanceState, action: AppearanceAction): AppearanceState {
  switch (action.type) {
    case 'color': return { ...state, scheme: getScheme(state.scheme) ? 'custom:unsaved' : state.scheme, colors: { ...state.colors, [action.key]: { ...state.colors[action.key], [action.mode]: action.value } } }
    case 'scalar': return { ...state, scalars: { ...state.scalars, [action.key]: action.value } }
    case 'select': return { ...state, selects: { ...state.selects, [action.key]: action.value } }
    case 'width': return { ...state, widthPreset: action.value }
    case 'scheme': return { ...state, scheme: action.scheme.id, colors: { ...action.scheme.colors } }
    case 'remove-scheme': return state.scheme === action.id ? { ...state, scheme: DEFAULT_SCHEME, colors: { ...getScheme(DEFAULT_SCHEME)?.colors } } : state
    case 'reset': {
      if (action.key === undefined) return defaultAppearance()
      const remove = <T,>(values: Record<string, T>) => Object.fromEntries(Object.entries(values).filter(([key]) => key !== action.key))
      return { ...state, colors: remove(state.colors), scalars: remove(state.scalars), selects: remove(state.selects) }
    }
  }
}

export function effectiveToken(state: AppearanceState, token: Token, mode: 'dark' | 'light'): string | number {
  switch (token.kind) {
    case 'color': return state.colors[token.varName]?.[mode] ?? token[mode]
    case 'scalar': return state.scalars[token.varName] ?? token.value
    case 'select': return state.selects[token.varName] ?? token.value
  }
}
export function snapshotColors(state: AppearanceState) {
  const dark: Record<string, string> = {}, light: Record<string, string> = {}
  for (const token of TOKENS) if (token.kind === 'color') {
    dark[token.varName] = String(effectiveToken(state, token, 'dark'))
    light[token.varName] = String(effectiveToken(state, token, 'light'))
  }
  return { dark, light }
}
export function themeToScheme(theme: ThemeRecord): Scheme {
  const colors: Scheme['colors'] = {}
  for (const token of TOKENS) if (token.kind === 'color' && (theme.dark?.[token.varName] != null || theme.light?.[token.varName] != null)) {
    colors[token.varName] = { dark: theme.dark?.[token.varName] ?? token.dark, light: theme.light?.[token.varName] ?? token.light }
  }
  return { id: `custom:${theme.slug}`, label: theme.name, emoji: theme.emoji, colors, swatch: colors['--color-primary'] ?? { dark: '#ff6b5b', light: '#e85a3f' } }
}
