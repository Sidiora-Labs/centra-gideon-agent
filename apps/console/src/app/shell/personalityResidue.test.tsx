
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render, waitFor } from '@testing-library/react'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

vi.mock('../../shared/data/api', () => ({
  api: { themes: () => new Promise(() => {}), theme: () => new Promise(() => {}) },
}))

document.title = 'Gideon'
const iconLink = document.createElement('link')
iconLink.setAttribute('rel', 'icon')
iconLink.setAttribute('href', '/gideon.svg')
document.head.appendChild(iconLink)

const { AppearanceProvider, useAppearance } = await import('./appearance')
const { PersonalityProvider, PersonalityShellElement, usePersonality } = await import('./personality')
const { DEFAULT_PERSONALITY, PERSONALITIES, PERSONALITY_DIAL_TOKENS, getPersonality } =
  await import('../../shared/theme/personalities')
const { TOKENS } = await import('../../shared/theme/tokenRegistry')

const WEB = process.cwd()
function tokenFor(varName: string) {
  const t = TOKENS.find((tk) => tk.varName === varName)
  if (!t) throw new Error(`no token declares ${varName}`)
  return t
}
const DENSITY_TOKEN = tokenFor('--ui-density')
const DIAL_TOKENS = Object.values(PERSONALITY_DIAL_TOKENS).map(tokenFor)
const DEFAULT_ENTRY = getPersonality(DEFAULT_PERSONALITY)!
const OTHERS = PERSONALITIES.filter((p) => p.id !== DEFAULT_PERSONALITY)
const NEUTRAL_SCHEME = 'ocean'

type Ctl = {
  activate: (id: string) => void
  pickScheme: (id: string) => void
  personalityId: string
  wordmarkLabel: string
  activeScheme: string
  density: string
  dials: string
}
let ctl: Ctl

function Probe() {
  const p = usePersonality()
  const a = useAppearance()
  ctl = {
    activate: p.activate,
    pickScheme: p.pickScheme,
    personalityId: p.personality.id,
    wordmarkLabel: p.wordmarkLabel,
    activeScheme: a.activeScheme,
    density: a.selectValue(DENSITY_TOKEN),
    dials: DIAL_TOKENS.map((t) => (t.kind === 'scalar' ? a.scalarValue(t) : a.selectValue(t))).join('|'),
  }
  return null
}

function mount() {
  return render(
    <AppearanceProvider>
      <PersonalityProvider>
        <Probe />
        <PersonalityShellElement />
      </PersonalityProvider>
    </AppearanceProvider>,
  )
}

function chrome() {
  return {
    title: document.title,
    favicon: document.querySelector<HTMLLinkElement>('link[rel~="icon"]')?.getAttribute('href') ?? null,
    dataPersonality: document.documentElement.dataset.personality ?? null,
    wordmark: ctl.wordmarkLabel,
    density: ctl.density,
    dials: ctl.dials,
  }
}

afterEach(() => {
  localStorage.clear()
  document.title = 'Gideon'
  iconLink.setAttribute('href', '/gideon.svg')
  document.documentElement.removeAttribute('data-personality')
})

describe('the residue population is real', () => {
  it('the jsdom document has the favicon link the restore path needs', () => {
    expect(document.querySelector('link[rel~="icon"]')).not.toBeNull()
  })

  it('at least one identity moves every value the restore assertions read', () => {
    expect(OTHERS.map((p) => p.id), 'no non-default identity exists — every test here is vacuous')
      .not.toEqual([])
    const movesTitle = OTHERS.filter((p) => p.behavior.documentTitle && p.behavior.documentTitle !== DEFAULT_ENTRY.behavior.documentTitle)
    const movesFavicon = OTHERS.filter((p) => p.behavior.faviconHref && p.behavior.faviconHref !== DEFAULT_ENTRY.behavior.faviconHref)
    const movesWordmark = OTHERS.filter((p) => p.behavior.wordmarkLabel !== DEFAULT_ENTRY.behavior.wordmarkLabel)
    const movesDials = OTHERS.filter((p) => p.behavior.dials && Object.keys(p.behavior.dials).length > 0)
    expect(movesTitle.map((p) => p.id), 'no identity changes the tab title').not.toEqual([])
    expect(movesFavicon.map((p) => p.id), 'no identity changes the favicon — the favicon restore is untested').not.toEqual([])
    expect(movesWordmark.map((p) => p.id), 'no identity changes the wordmark').not.toEqual([])
    expect(movesDials.map((p) => p.id), 'no identity moves a dial').not.toEqual([])
  })
})

describe.each(OTHERS.map((p) => [p.id] as const))('%s → a standard scheme', (id) => {
  const entry = getPersonality(id)!

  it('applies the identity, then leaves nothing behind when a plain scheme is picked', async () => {
    const { container } = mount()
    const pristine = chrome()
    expect(pristine.title).toBe('Gideon')
    expect(pristine.favicon).toBe('/gideon.svg')

    act(() => ctl.activate(id))
    const on = chrome()
    expect(on.dataPersonality).toBe(id)
    if (entry.behavior.documentTitle) expect(on.title).toBe(entry.behavior.documentTitle)
    if (entry.behavior.faviconHref) expect(on.favicon).toBe(entry.behavior.faviconHref)
    expect(on.wordmark).toBe(entry.behavior.wordmarkLabel)
    expect(ctl.activeScheme).toBe(entry.baseScheme)
    if (entry.behavior.shellElement) {
      await waitFor(() => expect(container.querySelectorAll('[data-shell-element]')).toHaveLength(1))
    }

    act(() => ctl.pickScheme(NEUTRAL_SCHEME))
    const off = chrome()
    expect(ctl.activeScheme, 'the picked scheme must still win — restoring must not undo the pick')
      .toBe(NEUTRAL_SCHEME)
    expect(off.title, 'residue 1/4: tab title').toBe(pristine.title)
    expect(off.favicon, 'residue 2/4: favicon').toBe(pristine.favicon)
    expect(off.wordmark, 'residue 3/4: product name in the shell').toBe(pristine.wordmark)
    expect(off.dataPersonality, 'residue 4/4: data-personality on <html>').toBe(DEFAULT_PERSONALITY)
    expect(off.density, 'residue 4/4: interface density').toBe(pristine.density)
    expect(off.dials, 'residue 4/4: motion + backdrop dials').toBe(pristine.dials)
    expect(container.querySelectorAll('[data-shell-element]'), 'residue 4/4: contributed shell element')
      .toHaveLength(0)
    expect(localStorage.getItem('personality')).toBe(DEFAULT_PERSONALITY)
  })

  it("stays on when the picked scheme is the identity's OWN base scheme", () => {
    mount()
    act(() => ctl.activate(id))
    act(() => ctl.pickScheme(entry.baseScheme))
    expect(ctl.personalityId, 'clicking the tile that is already lit must not drop the identity')
      .toBe(id)
    expect(document.documentElement.dataset.personality).toBe(id)
    expect(ctl.activeScheme).toBe(entry.baseScheme)
  })

  it('is taken with "reset everything to defaults"', () => {
    mount()
    act(() => ctl.activate(id))
    expect(document.documentElement.dataset.personality).toBe(id)
    act(() => ctl.activate(DEFAULT_PERSONALITY))
    expect(document.title).toBe('Gideon')
    expect(document.querySelector<HTMLLinkElement>('link[rel~="icon"]')?.getAttribute('href')).toBe('/gideon.svg')
    expect(document.documentElement.dataset.personality).toBe(DEFAULT_PERSONALITY)
    expect(ctl.wordmarkLabel).toBe('Gideon')
  })
})

describe('the Design panel has no way to change a scheme that skips the identity', () => {
  const src = readFileSync(join(WEB, 'src/features/settings/DesignPanel.tsx'), 'utf8')

  it('reads the panel it is asserting about', () => {
    expect(src.length, 'DesignPanel.tsx not found — this rail is measuring nothing').toBeGreaterThan(2000)
    expect(src).toContain('SchemeTile')
  })

  it('routes the scheme tiles through pickScheme', () => {
    expect(src).toContain('onPick={() => pickScheme(s.id)}')
  })

  it('never destructures applyScheme, the bypass that caused the residue', () => {
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code).not.toMatch(/applyScheme/)
  })

  it('takes the identity with "reset everything to defaults"', () => {
    expect(src).toContain('onClick={resetEverything}')
    expect(src).toMatch(/resetEverything = \(\) => \{[\s\S]*activate\(DEFAULT_PERSONALITY\)[\s\S]*resetAll\(\)/)
  })
})

describe('every declared favicon is a file the gateway actually serves', () => {
  // 🔴 All three identities declared `/favicon.svg`, which exists NOWHERE in the repo.
  // `web/public/` has only `gideon.svg`, and the gateway routes exactly one dist-root SVG
  // (`/gideon.svg`) plus the `/icons/` directory — so `GET /favicon.svg` fell through to
  // the SPA catch-all and returned **200 text/html, 15860 bytes** of index.html. Measured
  // on the live gateway during the PT-6 tour. A 200 means no console error and no failed
  // request: the only symptom was a tab with no mark.
  const ROUTED = ['/gideon.svg', '/icons/']

  it.each(PERSONALITIES.filter((p) => p.behavior.faviconHref).map((p) => [p.id, p.behavior.faviconHref!] as const))(
    '%s → %s exists and sits under a routed prefix',
    (_id, href) => {
      expect(href.startsWith('/'), 'a favicon must be a bundled local path').toBe(true)
      expect(existsSync(join(WEB, 'public', href.slice(1)))).toBe(true)
      expect(
        ROUTED.some((r) => (r.endsWith('/') ? href.startsWith(r) : href === r)),
        `${href} is not under a static route (${ROUTED.join(', ')}), so the gateway would ` +
          `serve index.html for it — a 200 that is not an image`,
      ).toBe(true)
    },
  )

  it('at least one identity declares a favicon of its own', () => {
    const distinct = OTHERS.filter((p) => p.behavior.faviconHref && p.behavior.faviconHref !== DEFAULT_ENTRY.behavior.faviconHref)
    expect(distinct.map((p) => p.id), 'every identity shares the default favicon — the swap and its restore are both untested')
      .not.toEqual([])
  })
})
