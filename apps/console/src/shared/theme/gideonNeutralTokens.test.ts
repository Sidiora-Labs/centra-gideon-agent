import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DEFAULT_SCHEME, getScheme, SCHEMES } from './schemes'
import { TOKENS } from './tokenRegistry'
import { defaultAppearance, effectiveToken } from '../../app/shell/appearanceState'
import { AppearanceProvider } from '../../app/shell/appearance'
import { ThemeProvider } from '../../app/shell/theme'

const css = readFileSync(join(process.cwd(), 'src/shared/theme/tokens.css'), 'utf8')
const dark = css.match(/@theme\s*\{([\s\S]*?)\n\}/)?.[1] ?? ''
const light = css.match(/\.light\s*\{([\s\S]*?)\n\}/)?.[1] ?? ''

function token(scope: string, name: string): string {
  const value = scope.match(new RegExp(`(?:^|\\n)\\s*${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`))?.[1]
  if (!value) throw new Error(`${name} is missing or not a concrete color`)
  return value.toLowerCase()
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((offset) => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
}

function contrast(a: string, b: string): number {
  const values = [luminance(a), luminance(b)].sort((x, y) => x - y)
  return (values[1] + 0.05) / (values[0] + 0.05)
}

const expected = {
  dark: {
    canvas: '#19191c', rail: '#111113', surface: '#202024', 'surface-container': '#202024',
    'surface-high': '#29292e', 'surface-highest': '#303036', 'on-surface': '#eeeeef',
    'on-surface-low': '#a2a2ad', 'outline-variant': '#34343b', primary: '#d9dbeb',
    'on-primary': '#1c1d26', 'primary-emphasis': '#e4e5f0',
  },
  light: {
    canvas: '#fcfcfd', rail: '#f3f3f5', surface: '#f5f5f7', 'surface-container': '#f5f5f7',
    'surface-high': '#ededf0', 'surface-highest': '#e7e7ec', 'on-surface': '#202027',
    'on-surface-low': '#62626f', 'outline-variant': '#dddde5', primary: '#343e69',
    'on-primary': '#ffffff', 'primary-emphasis': '#303a63',
  },
} as const

describe('Gideon neutral shell tokens', () => {
  it.each(['dark', 'light'] as const)('%s uses the approved graphite surface and ink palette', (mode) => {
    const scope = mode === 'dark' ? dark : light
    for (const [name, value] of Object.entries(expected[mode])) {
      expect(token(scope, `--color-${name}`), `${mode} ${name}`).toBe(value)
    }
  })

  it.each(['dark', 'light'] as const)('%s keeps foreground, muted text and button ink legible', (mode) => {
    const scope = mode === 'dark' ? dark : light
    for (const surface of ['canvas', 'rail', 'surface-container', 'surface-high']) {
      expect(contrast(token(scope, '--color-on-surface'), token(scope, `--color-${surface}`)), `${mode} text on ${surface}`)
        .toBeGreaterThanOrEqual(7)
      expect(contrast(token(scope, '--color-on-surface-low'), token(scope, `--color-${surface}`)), `${mode} muted on ${surface}`)
        .toBeGreaterThanOrEqual(4.5)
    }
    expect(contrast(token(scope, '--color-primary'), token(scope, '--color-on-primary')), `${mode} filled accent`)
      .toBeGreaterThanOrEqual(4.5)
    expect(contrast(token(scope, '--color-primary-emphasis'), token(scope, '--color-surface-high')), `${mode} focus and accent text`)
      .toBeGreaterThanOrEqual(4.5)
  })

  it('the default scheme matches both CSS modes for every accent-driving token', () => {
    expect(DEFAULT_SCHEME).toBe('gideon')
    const scheme = getScheme(DEFAULT_SCHEME)
    expect(scheme).toBeDefined()
    for (const mode of ['dark', 'light'] as const) {
      const scope = mode === 'dark' ? dark : light
      for (const [name, colors] of Object.entries(scheme!.colors)) {
        expect(token(scope, name), `${mode} ${name}`).toBe(colors[mode])
      }
    }
  })

  it('preserves the curated alternate schemes as selectable pairs', () => {
    expect(SCHEMES.map((scheme) => scheme.id)).toEqual([
      'gideon', 'coral', 'honey', 'jade', 'ember', 'lavender', 'ocean',
      'forest', 'rose', 'amber', 'slate', 'mono', 'phosphor',
    ])
    expect(getScheme('coral')?.colors['--color-primary']).toEqual({ dark: '#ff6b5b', light: '#c8452e' })
    expect(getScheme('mono')?.colors['--color-primary']).toEqual({ dark: '#d4d4d4', light: '#3a3a3a' })
    for (const scheme of SCHEMES) {
      expect(scheme.swatch.dark).toBe(scheme.colors['--color-primary'].dark)
      expect(scheme.swatch.light).toBe(scheme.colors['--color-primary'].light)
    }
  })

  it('the appearance registry supplies every CSS default to the live override path', () => {
    const state = defaultAppearance()
    for (const entry of TOKENS) {
      if (entry.kind !== 'color') continue
      expect(effectiveToken(state, entry, 'dark'), `dark ${entry.varName}`).toBe(token(dark, entry.varName))
      expect(effectiveToken(state, entry, 'light'), `light ${entry.varName}`).toBe(token(light, entry.varName))
    }
  })

  it.each(['dark', 'light'] as const)('%s persistence reaches the document through the real appearance provider', (mode) => {
    localStorage.setItem('mode', mode)
    localStorage.removeItem('appearance')
    const view = render(createElement(ThemeProvider, null,
      createElement(AppearanceProvider, null, createElement('span', null, 'Gideon'))))
    const root = document.documentElement
    expect(root.dataset.mode).toBe(mode)
    expect(root.classList.contains('light')).toBe(mode === 'light')
    for (const name of ['--color-canvas', '--color-surface-container', '--color-primary', '--color-on-surface']) {
      expect(root.style.getPropertyValue(name), `${mode} applied ${name}`)
        .toBe(token(mode === 'dark' ? dark : light, name))
    }
    expect(JSON.parse(localStorage.getItem('appearance')!).scheme).toBe(DEFAULT_SCHEME)
    view.unmount()
  })

  it('a persisted alternate scheme retains its accent over the new neutral surfaces', () => {
    localStorage.setItem('mode', 'light')
    localStorage.setItem('appearance', JSON.stringify({ scheme: 'coral', colors: getScheme('coral')!.colors }))
    const view = render(createElement(ThemeProvider, null,
      createElement(AppearanceProvider, null, createElement('span', null, 'Gideon'))))
    const root = document.documentElement
    expect(root.dataset.theme).toBe('coral')
    expect(root.style.getPropertyValue('--color-primary')).toBe('#c8452e')
    expect(root.style.getPropertyValue('--color-surface-container')).toBe('#f5f5f7')
    expect(root.style.getPropertyValue('--color-on-surface')).toBe('#202027')
    view.unmount()
  })

  it('leaves inherited color scheme, typography and focus mappings in place', () => {
    expect(css).toMatch(/:root:not\(\.light\)\s*\{\s*color-scheme:\s*dark/)
    expect(light).toContain('color-scheme: light')
    expect(dark).toContain('--font-sans: "Inter", system-ui, sans-serif;')
    expect(dark).toContain('--radius-md: calc(12px * var(--radius-scale) * var(--radius-density));')
    if (css.includes('--color-ring:')) expect(css).toContain('--color-ring: var(--color-primary);')
  })
})
