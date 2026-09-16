import { attachThemeFavicon } from '../../shared/ui/gideonIdentity'
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, type ReactNode } from 'react'

export type Mode = 'dark' | 'light'
export type Preference = Mode | 'auto'
export const DEFAULT_PREFERENCE: Preference = 'dark'
const preferenceOf = (value: unknown): Preference | undefined => value === 'dark' || value === 'light' || value === 'auto' ? value : undefined
interface ThemeState { preference: Preference; system: Mode }
type ThemeAction = { type: 'preference'; value: Preference } | { type: 'system'; value: Mode } | { type: 'toggle' }
export function resolveMode(state: ThemeState): Mode { return state.preference === 'auto' ? state.system : state.preference }
export function themeReducer(state: ThemeState, action: ThemeAction): ThemeState {
  if (action.type === 'system') return { ...state, system: action.value }
  return { ...state, preference: action.type === 'toggle' ? (resolveMode(state) === 'dark' ? 'light' : 'dark') : action.value }
}
interface Ctx { mode: Mode; preference: Preference; toggle: () => void; setPreference: (value: Preference) => void }
const ThemeCtx = createContext<Ctx>({ mode: 'dark', preference: DEFAULT_PREFERENCE, toggle: () => {}, setPreference: () => {} })
const mediaQuery = () => window.matchMedia?.('(prefers-color-scheme: light)')
function initialTheme(): ThemeState {
  let preference = DEFAULT_PREFERENCE
  try { preference = preferenceOf(localStorage.getItem('mode')) ?? DEFAULT_PREFERENCE } catch { /* Storage is optional. */ }
  return { preference, system: mediaQuery()?.matches ? 'light' : 'dark' }
}
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(themeReducer, undefined, initialTheme)
  const mode = resolveMode(state)
  useEffect(() => {
    const media = mediaQuery()
    const onSystem = () => dispatch({ type: 'system', value: media?.matches ? 'light' : 'dark' })
    const onStorage = (event: StorageEvent) => {
      const value = preferenceOf(event.newValue)
      if (event.key === 'mode' && value) dispatch({ type: 'preference', value })
    }
    media?.addEventListener?.('change', onSystem)
    window.addEventListener('storage', onStorage)
    return () => { media?.removeEventListener?.('change', onSystem); window.removeEventListener('storage', onStorage) }
  }, [])
  useEffect(() => {
    document.documentElement.classList.toggle('light', mode === 'light')
    document.documentElement.dataset.mode = mode
  }, [mode])
  useEffect(() => attachThemeFavicon(mode), [mode])
  useEffect(() => { try { localStorage.setItem('mode', state.preference) } catch { /* Storage is optional. */ } }, [state.preference])
  const toggle = useCallback(() => dispatch({ type: 'toggle' }), [])
  const setPreference = useCallback((value: Preference) => dispatch({ type: 'preference', value }), [])
  const value = useMemo(() => ({ mode, preference: state.preference, toggle, setPreference }), [mode, state.preference, toggle, setPreference])
  return <ThemeCtx.Provider value={value}>{children}</ThemeCtx.Provider>
}
export const useMode = () => useContext(ThemeCtx)
