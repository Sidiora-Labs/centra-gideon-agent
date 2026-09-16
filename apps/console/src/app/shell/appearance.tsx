import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from 'react'
import { TOKENS, type Token } from '../../shared/theme/tokenRegistry'
import { runtime } from '../../shared/theme/runtime'
import { SCHEMES, getScheme, type Scheme } from '../../shared/theme/schemes'
import { api } from '../../shared/data/api'
import { useMode } from './theme'
import { useIsMobile } from './useIsMobile'
import { appearanceReducer, effectiveToken, parseAppearance, snapshotColors, themeToScheme, WIDTH_PRESETS, type AppearanceState, type WidthPreset } from './appearanceState'
export { DEFAULT_WIDTH_PRESET, WIDTH_PRESETS, type WidthPreset } from './appearanceState'

interface Ctx {
  colorValue: (token: Token, mode: 'dark' | 'light') => string
  scalarValue: (token: Token) => number
  selectValue: (token: Token) => string
  setColor: (key: string, mode: 'dark' | 'light', value: string) => void
  setScalar: (key: string, value: number) => void
  setSelect: (key: string, value: string) => void
  resetAll: () => void
  resetToken: (key: string) => void
  widthPreset: WidthPreset
  setWidthPreset: (value: WidthPreset) => void
  activeScheme: string
  allSchemes: Scheme[]
  applyScheme: (id: string) => void
  saveCustomScheme: (label: string, emoji?: string) => Promise<string>
  updateCustomScheme: (id: string, label: string, emoji?: string) => Promise<void>
  deleteCustomScheme: (id: string) => Promise<void>
  themesLoading: boolean
}
const AppearanceCtx = createContext<Ctx>(null as unknown as Ctx)
const FONTS: Record<string, string> = {
  'dm-sans': '"DM Sans", system-ui, sans-serif', inter: '"Inter", system-ui, sans-serif',
  mono: '"JetBrains Mono", ui-monospace, monospace', system: 'system-ui, -apple-system, "Segoe UI", sans-serif',
}
const rgb = (hex: string): [number, number, number] => {
  const digits = hex.replace('#', '')
  const full = digits.length === 3 ? [...digits].map((part) => part.repeat(2)).join('') : digits
  return [0, 2, 4].map((offset) => parseInt(full.slice(offset, offset + 2), 16)) as [number, number, number]
}
function applyAppearance(state: AppearanceState, mode: 'dark' | 'light', mobile: boolean) {
  const root = document.documentElement
  const properties = new Map<string, string>()
  for (const token of TOKENS) {
    const value = effectiveToken(state, token, mode)
    properties.set(token.varName, token.kind === 'scalar' ? `${value}${token.unit ?? ''}` : String(value))
    if (token.kind === 'scalar' && token.runtimeKey) runtime[token.runtimeKey] = Number(value)
    if (token.kind === 'select' && token.runtimeKey === 'dotShape') runtime.dotShape = value as typeof runtime.dotShape
    if (token.kind === 'select' && token.runtimeKey === 'dotPattern') runtime.dotPattern = value as typeof runtime.dotPattern
  }
  runtime.glowA = rgb(properties.get('--glow-a')!)
  runtime.glowB = rgb(properties.get('--grad-3')!)
  properties.set('zoom', String((state.scalars['--ui-zoom'] ?? 100) / 100))
  properties.set('font-size', `${state.scalars['--font-scale'] ?? 100}%`)
  properties.set('--content-width', mobile ? '100%' : WIDTH_PRESETS[state.widthPreset])
  const font = state.selects['--font-family']
  if (font) properties.set('--font-sans', FONTS[font] ?? FONTS['dm-sans'])
  else root.style.removeProperty('--font-sans')
  properties.forEach((value, key) => root.style.setProperty(key, value))
  root.dataset.theme = state.scheme
  root.dataset.ui = state.selects['--ui-density'] ?? 'comfortable'
}

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const { mode } = useMode()
  const mobile = useIsMobile()
  const [state, dispatch] = useReducer(appearanceReducer, undefined, () => {
    try { return parseAppearance(localStorage.getItem('appearance')) } catch { return parseAppearance(null) }
  })
  const current = useRef(state)
  current.current = state
  const [themes, setThemes] = useState<Scheme[]>([])
  const [themesLoading, setThemesLoading] = useState(true)
  const generation = useRef(0)
  const reloadThemes = useCallback(async () => {
    const request = ++generation.current
    try {
      const summaries = await api.themes()
      const records = await Promise.all(summaries.map(({ slug }) => api.theme(slug)))
      if (request === generation.current) setThemes(records.map(themeToScheme))
    } catch { /* Curated schemes remain available offline. */ }
    finally { if (request === generation.current) setThemesLoading(false) }
  }, [])
  useEffect(() => { void reloadThemes(); return () => { generation.current += 1 } }, [reloadThemes])
  useEffect(() => { applyAppearance(state, mode, mobile) }, [state, mode, mobile])
  useEffect(() => { try { localStorage.setItem('appearance', JSON.stringify(state)) } catch { /* Keep this session usable. */ } }, [state])

  const commands = useMemo(() => ({
    setColor: (key: string, mode: 'dark' | 'light', value: string) => dispatch({ type: 'color', key, mode, value }),
    setScalar: (key: string, value: number) => dispatch({ type: 'scalar', key, value }),
    setSelect: (key: string, value: string) => dispatch({ type: 'select', key, value }),
    setWidthPreset: (value: WidthPreset) => dispatch({ type: 'width', value }),
    resetAll: () => dispatch({ type: 'reset' }),
    resetToken: (key: string) => dispatch({ type: 'reset', key }),
  }), [])
  const applyScheme = useCallback((id: string) => {
    const scheme = getScheme(id) ?? themes.find((entry) => entry.id === id)
    if (scheme) dispatch({ type: 'scheme', scheme })
  }, [themes])
  const saveCustomScheme = useCallback(async (label: string, emoji?: string) => {
    const result = await api.createTheme({ name: label.trim() || 'Custom', emoji, ...snapshotColors(current.current) })
    await reloadThemes()
    dispatch({ type: 'scheme', scheme: themeToScheme(result.theme) })
    return `custom:${result.slug}`
  }, [reloadThemes])
  const updateCustomScheme = useCallback(async (id: string, label: string, emoji?: string) => {
    const result = await api.updateTheme(id.replace(/^custom:/, ''), { name: label.trim() || 'Custom', emoji, ...snapshotColors(current.current) })
    await reloadThemes()
    dispatch({ type: 'scheme', scheme: { ...themeToScheme(result.theme), id } })
  }, [reloadThemes])
  const deleteCustomScheme = useCallback(async (id: string) => {
    await api.deleteTheme(id.replace(/^custom:/, ''))
    await reloadThemes()
    dispatch({ type: 'remove-scheme', id })
  }, [reloadThemes])
  const value = useMemo<Ctx>(() => ({
    ...commands, applyScheme, saveCustomScheme, updateCustomScheme, deleteCustomScheme,
    colorValue: (token, selectedMode) => token.kind === 'color' ? String(effectiveToken(state, token, selectedMode)) : '',
    scalarValue: (token) => token.kind === 'scalar' ? Number(effectiveToken(state, token, mode)) : 0,
    selectValue: (token) => token.kind === 'select' ? String(effectiveToken(state, token, mode)) : '',
    widthPreset: state.widthPreset, activeScheme: state.scheme, allSchemes: [...SCHEMES, ...themes], themesLoading,
  }), [state, mode, themes, themesLoading, commands, applyScheme, saveCustomScheme, updateCustomScheme, deleteCustomScheme])
  return <AppearanceCtx.Provider value={value}>{children}</AppearanceCtx.Provider>
}
export const useAppearance = () => useContext(AppearanceCtx)
