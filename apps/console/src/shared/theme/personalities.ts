
import { lazy, type ComponentType, type LazyExoticComponent } from 'react'

import type { ErrorTreatmentId } from './errorTreatments'
import type { DotPattern, DotShape } from './runtime'
import type { CueName, CuePoint } from './soundCues'

export type ShellElementId = 'terminal-scanlines'

export const SHELL_ELEMENTS: Record<ShellElementId, LazyExoticComponent<ComponentType>> = {
  'terminal-scanlines': lazy(() =>
    import('../ui/personality/TerminalStrip').then((m) => ({ default: m.TerminalStrip })),
  ),
}

export function getShellElement(id: string | undefined): LazyExoticComponent<ComponentType> | null {
  if (!id || !Object.hasOwn(SHELL_ELEMENTS, id)) return null
  return SHELL_ELEMENTS[id as ShellElementId]
}

export interface PersonalityDials {
  expressiveness?: number
  bounciness?: number
  dotShape?: DotShape
  dotPattern?: DotPattern
}

export const PERSONALITY_DIAL_TOKENS: Record<keyof PersonalityDials, string> = {
  expressiveness: '--expressiveness',
  bounciness: '--bounciness',
  dotShape: '--dot-shape',
  dotPattern: '--dot-pattern',
}

export interface PersonalityBehavior {
  displayName?: string
  wordmarkLabel?: string
  /** Favicon path — bundled assets under `web/public/` only, never a remote URL.
   *
   *  MUST be a path the gateway actually serves, which is narrower than "a file in
   *  `web/public/`": only `/gideon.svg` and the `/icons/` directory have static routes
   *  (`dashboard/server.py`), and every other dist-root path falls through to the SPA
   *  catch-all. A wrong path therefore returns **200 with `text/html`** — index.html
   *  served as an icon — so the tab silently loses its mark with nothing in the console
   *  to say why. `personalityResidue.test.tsx` pins both halves (the file exists, and it
   *  sits under a routed prefix). */
  faviconHref?: string
  personaSnippet?: string
  uiDensity?: 'comfortable' | 'dense' | 'cli'
  documentTitle?: string
  shellElement?: ShellElementId
  errorTreatment?: ErrorTreatmentId
  soundCues?: Partial<Record<CuePoint, CueName>>
  dials?: PersonalityDials
}

export interface Personality {
  id: string
  label: string
  hint: string
  baseScheme: string
  behavior: PersonalityBehavior
}

export const DEFAULT_PERSONALITY = 'gideon'

export const PERSONALITIES: Personality[] = [
  {
    id: DEFAULT_PERSONALITY,
    label: 'Gideon',
    hint: 'The default identity — coral, the Gideon wordmark, no persona.',
    baseScheme: 'coral',
    behavior: {
      wordmarkLabel: 'Gideon',
      documentTitle: 'Gideon',
      faviconHref: '/gideon.svg',
    },
  },
  {
    id: 'retro-terminal',
    label: 'Retro Terminal',
    hint: 'Mono-green phosphor, dense CLI spacing, a scanline haze, and a terse operator voice.',
    baseScheme: 'phosphor',
    behavior: {
      displayName: 'TERM',
      wordmarkLabel: 'TERM://PC',
      documentTitle: 'TERM://Gideon',
      faviconHref: '/icons/personality-retro-terminal.svg',
      personaSnippet: 'persona-retro-terminal',
      uiDensity: 'cli',
      shellElement: 'terminal-scanlines',
      errorTreatment: 'terminal-frame',
      soundCues: { approval_needed: 'terminal_bell' },
      dials: { expressiveness: 0.25, bounciness: 0, dotShape: 'square', dotPattern: 'grid' },
    },
  },
  {
    id: 'gideon-arcade',
    label: 'Gideon Arcade',
    hint: 'Amber cabinet glow, sparkle dots, bouncy motion, and a playful, high-energy voice.',
    baseScheme: 'amber',
    behavior: {
      displayName: 'GIDEON-1',
      wordmarkLabel: 'GIDEON ARCADE',
      documentTitle: 'GIDEON ARCADE',
      faviconHref: '/icons/personality-gideon-arcade.svg',
      uiDensity: 'comfortable',
      errorTreatment: 'arcade-panel',
      soundCues: { turn_complete: 'coin_blip' },
      dials: { expressiveness: 1, bounciness: 1, dotShape: 'sparkle', dotPattern: 'diamond' },
    },
  },
]

export function getPersonality(id: string | undefined): Personality | undefined {
  return PERSONALITIES.find((p) => p.id === id)
}

export function resolvePersonality(id: string | undefined): Personality {
  return getPersonality(id) ?? getPersonality(DEFAULT_PERSONALITY) ?? PERSONALITIES[0]
}
