import { createContext, Suspense, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useAppearance } from './appearance'
import { DEFAULT_PERSONALITY, PERSONALITIES, PERSONALITY_DIAL_TOKENS, getShellElement, resolvePersonality, type Personality } from '../../shared/theme/personalities'
import { getErrorTreatment, type ErrorTreatment } from '../../shared/theme/errorTreatments'
import { setCueVoices } from '../../shared/theme/soundCues'

interface Ctx {
  personality: Personality
  all: Personality[]
  activate: (id: string) => void
  pickScheme: (schemeId: string) => void
  wordmarkLabel: string
}
const PersonalityCtx = createContext<Ctx | null>(null)
const baseline = {
  title: typeof document === 'undefined' ? 'Gideon' : document.title,
  favicon: typeof document === 'undefined' ? null : document.querySelector('link[rel~="icon"]')?.getAttribute('href') ?? null,
}
const readPersonality = () => {
  try { return resolvePersonality(localStorage.getItem('personality') || DEFAULT_PERSONALITY) }
  catch { return resolvePersonality(DEFAULT_PERSONALITY) }
}
function applyChrome(personality: Personality) {
  const behavior = personality.id === DEFAULT_PERSONALITY ? {} : personality.behavior
  document.title = behavior.documentTitle ?? baseline.title
  const favicon = document.querySelector('link[rel~="icon"]')
  const href = behavior.faviconHref ?? baseline.favicon
  if (href) favicon?.setAttribute('href', href)
  else favicon?.removeAttribute('href')
  document.documentElement.dataset.personality = personality.id
  setCueVoices(personality.behavior.soundCues)
}
export function PersonalityProvider({ children }: { children: ReactNode }) {
  const { applyScheme, setSelect, setScalar, resetToken } = useAppearance()
  const [personality, setPersonality] = useState(readPersonality)
  useEffect(() => { applyChrome(personality) }, [personality])
  const activate = useCallback((id: string) => {
    const selected = resolvePersonality(id)
    applyScheme(selected.baseScheme)
    setSelect('--ui-density', selected.behavior.uiDensity ?? 'comfortable')
    for (const [name, key] of Object.entries(PERSONALITY_DIAL_TOKENS)) {
      const value = selected.behavior.dials?.[name as keyof typeof PERSONALITY_DIAL_TOKENS]
      if (typeof value === 'number') setScalar(key, value)
      else if (typeof value === 'string') setSelect(key, value)
      else resetToken(key)
    }
    setPersonality(selected)
    try { localStorage.setItem('personality', selected.id) } catch { /* Session selection still applies. */ }
  }, [applyScheme, setSelect, setScalar, resetToken])
  const pickScheme = useCallback((id: string) => {
    if (id !== personality.baseScheme) activate(DEFAULT_PERSONALITY)
    applyScheme(id)
  }, [personality.baseScheme, activate, applyScheme])
  const value = useMemo(() => ({ personality, all: PERSONALITIES, activate, pickScheme, wordmarkLabel: personality.behavior.wordmarkLabel ?? 'Gideon' }), [personality, activate, pickScheme])
  return <PersonalityCtx.Provider value={value}>{children}</PersonalityCtx.Provider>
}
const idleCommands = { activate: () => {}, pickScheme: () => {} }
export function usePersonality(): Ctx {
  const context = useContext(PersonalityCtx)
  return context ?? { ...idleCommands, personality: resolvePersonality(DEFAULT_PERSONALITY), all: PERSONALITIES, wordmarkLabel: 'Gideon' }
}
export function PersonalityShellElement() {
  const Element = getShellElement(usePersonality().personality.behavior.shellElement)
  return Element ? <Suspense fallback={null}><Element /></Suspense> : null
}
export function useErrorTreatment(): ErrorTreatment | null {
  const context = useContext(PersonalityCtx)
  try {
    const personality = context?.personality ?? resolvePersonality(DEFAULT_PERSONALITY)
    return getErrorTreatment(personality.behavior.errorTreatment)
  } catch { return null }
}
