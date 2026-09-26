/**
 * Curated color SCHEMES. A scheme is a named set of color-token overrides — it
 * retints the brand + glow/gradient identity (accent, primary, focus ring, the
 * spatial-backdrop glow) while leaving surfaces / content / layout coherent. The
 * user picks one; advanced users fork the current scheme into a saved custom one.
 *
 * Each scheme provides {dark, light} for the accent-driving tokens. The keys map
 * 1:1 to tokenRegistry color varNames; the appearance store applies them as
 * overrides (so a scheme = a known override set, and "reset" = the default
 * scheme 'gideon').
 */

export interface Scheme {
  id: string
  label: string
  emoji?: string
  swatch: { dark: string; light: string }
  colors: Record<string, { dark: string; light: string }>
}

function scheme(id: string, label: string, s: {
  primary: [string, string]
  primaryEmphasis: [string, string]
  onPrimary: [string, string]
  primaryContainer: [string, string]
  secondary: [string, string]
  gradient: [string, string, string, string]
  glowA: [string, string]
  glowB: [string, string]
  info: [string, string]
}): Scheme {
  const dl = (d: string, l: string) => ({ dark: d, light: l })
  return {
    id, label,
    swatch: { dark: s.primary[0], light: s.primary[1] },
    colors: {
      '--color-primary': dl(s.primary[0], s.primary[1]),
      '--color-primary-emphasis': dl(s.primaryEmphasis[0], s.primaryEmphasis[1]),
      '--color-on-primary': dl(s.onPrimary[0], s.onPrimary[1]),
      '--color-primary-container': dl(s.primaryContainer[0], s.primaryContainer[1]),
      '--color-secondary': dl(s.secondary[0], s.secondary[1]),
      '--color-info': dl(s.info[0], s.info[1]),
      '--grad-1': dl(s.gradient[0], s.gradient[0]),
      '--grad-2': dl(s.gradient[1], s.primary[1]),
      '--grad-3': dl(s.gradient[2], s.glowB[1]),
      '--grad-4': dl(s.gradient[3], s.gradient[3]),
      '--glow-a': dl(s.glowA[0], s.glowA[1]),
      '--glow-b': dl(s.glowB[0], s.glowB[1]),
    },
  }
}

export const SCHEMES: Scheme[] = [
  scheme('gideon', 'Gideon', {
    primary: ['#d9dbeb', '#343e69'], primaryEmphasis: ['#e4e5f0', '#303a63'],
    onPrimary: ['#1c1d26', '#ffffff'], primaryContainer: ['#343546', '#e5e7f0'],
    secondary: ['#b5b8ce', '#4a527e'], info: ['#aeb8e6', '#3f5185'],
    gradient: ['#777d9f', '#d9dbeb', '#aeb8e6', '#b5b8ce'],
    glowA: ['#d9dbeb', '#343e69'], glowB: ['#aeb8e6', '#59648f'],
  }),
  // Coral remains available alongside the Gideon default.
  // Light primary/emphasis/info mirror tokenRegistry's AA-verified shades
  // (≥4.5:1 as white-text button fill AND as text on white) — keep in sync.
  // Glow/gradient stay on the brighter coral: decorative, not text-bearing.
  scheme('coral', 'Coral', {
    primary: ['#ff6b5b', '#c8452e'], primaryEmphasis: ['#ff9a86', '#a33922'],
    onPrimary: ['#3f1008', '#ffffff'], primaryContainer: ['#5a1d12', '#ffe0d6'],
    secondary: ['#ffb454', '#cf7a23'], info: ['#629efe', '#1059bc'],
    gradient: ['#c85a48', '#ff6b5b', '#ff9a7a', '#ffb454'],
    glowA: ['#ff6b5b', '#e85a3f'], glowB: ['#ff9a7a', '#e07a54'],
  }),
  scheme('honey', 'Honey', {
    primary: ['#f2a93b', '#9d6614'], primaryEmphasis: ['#ffca7a', '#985e10'],
    onPrimary: ['#3a2504', '#ffffff'], primaryContainer: ['#523611', '#ffe9c2'],
    secondary: ['#e8785a', '#c2503a'], info: ['#50a1fa', '#0057c2'],
    gradient: ['#b0832f', '#f2a93b', '#ffca7a', '#e8785a'],
    glowA: ['#f2a93b', '#c17d18'], glowB: ['#ffca7a', '#d89a4a'],
  }),
  scheme('jade', 'Jade', {
    primary: ['#2dd4bf', '#0b7f75'], primaryEmphasis: ['#7fe8da', '#0a7268'],
    onPrimary: ['#04231f', '#ffffff'], primaryContainer: ['#0c3b35', '#cbf5ee'],
    secondary: ['#4e9ff8', '#1668d8'], info: ['#50a1fa', '#0057c2'],
    gradient: ['#2a9e90', '#2dd4bf', '#7fe8da', '#4e9ff8'],
    glowA: ['#2dd4bf', '#0d9488'], glowB: ['#7fe8da', '#3aa898'],
  }),
  scheme('ember', 'Ember (mono + spark)', {
    primary: ['#ff7a5c', '#c5482e'], primaryEmphasis: ['#ffa98f', '#b0432c'],
    onPrimary: ['#2a0f08', '#ffffff'], primaryContainer: ['#3a2018', '#f0ddd6'],
    secondary: ['#9a9a96', '#5a5a56'], info: ['#99a0a8', '#565c64'],
    gradient: ['#6a6560', '#ff7a5c', '#b09a92', '#8a8580'],
    glowA: ['#ff7a5c', '#d1543a'], glowB: ['#b0a49e', '#8a7f78'],
  }),
  scheme('lavender', 'Lavender', {
    primary: ['#9d8bff', '#6a4fd0'], primaryEmphasis: ['#b6bdff', '#563bbf'],
    onPrimary: ['#21134f', '#ffffff'], primaryContainer: ['#2e2168', '#e7deff'],
    secondary: ['#4e8ff8', '#1668d8'], info: ['#619eff', '#0057c2'],
    gradient: ['#8e75b2', '#9d8bff', '#c597ff', '#d8627e'],
    glowA: ['#9d8bff', '#6a4fd0'], glowB: ['#c597ff', '#9168c0'],
  }),
  scheme('ocean', 'Ocean', {
    primary: ['#4aa8ff', '#1668d8'], primaryEmphasis: ['#86c6ff', '#0e4fa8'],
    onPrimary: ['#04243f', '#ffffff'], primaryContainer: ['#0e3358', '#d8ecff'],
    secondary: ['#28c2c8', '#0a8f95'], info: ['#4aa8ff', '#0057c2'],
    gradient: ['#3a7bb0', '#4aa8ff', '#7fd0ff', '#28c2c8'],
    glowA: ['#4aa8ff', '#1668d8'], glowB: ['#7fd0ff', '#3a92c8'],
  }),
  scheme('forest', 'Forest', {
    primary: ['#4fc97f', '#1a824a'], primaryEmphasis: ['#86e0a6', '#157a44'],
    onPrimary: ['#06280f', '#ffffff'], primaryContainer: ['#0f3a22', '#d6f3e0'],
    secondary: ['#9bcf3a', '#5f8f15'], info: ['#3ab0a0', '#02695e'],
    gradient: ['#3a8f5e', '#4fc97f', '#9bdf6f', '#d8c24a'],
    glowA: ['#4fc97f', '#1f9b58'], glowB: ['#9bdf6f', '#5f9f3a'],
  }),
  scheme('rose', 'Rose', {
    primary: ['#ff7eb0', '#d22b6f'], primaryEmphasis: ['#ffb0cf', '#bf2f6a'],
    onPrimary: ['#3f0a22', '#ffffff'], primaryContainer: ['#5a1638', '#ffdcec'],
    secondary: ['#c597ff', '#8b5cd8'], info: ['#8496ff', '#4350c6'],
    gradient: ['#b0567e', '#ff7eb0', '#ffb0cf', '#c597ff'],
    glowA: ['#ff7eb0', '#d8407e'], glowB: ['#ffa6c8', '#c2607e'],
  }),
  scheme('amber', 'Amber', {
    primary: ['#ffb454', '#a5611c'], primaryEmphasis: ['#ffd08a', '#a15817'],
    onPrimary: ['#3f2404', '#ffffff'], primaryContainer: ['#5a3810', '#ffe8c8'],
    secondary: ['#f55e57', '#c8362f'], info: ['#ffb454', '#8d4d01'],
    gradient: ['#b07a3a', '#ffb454', '#ffd08a', '#f55e57'],
    glowA: ['#ffb454', '#cf7a23'], glowB: ['#ffce7f', '#d89a4a'],
  }),
  scheme('slate', 'Slate', {
    primary: ['#9aa6b8', '#5a6a82'], primaryEmphasis: ['#c0c8d4', '#46556e'],
    onPrimary: ['#1a212e', '#ffffff'], primaryContainer: ['#2a3340', '#dde3ec'],
    secondary: ['#7f9cff', '#4f6fd8'], info: ['#7f9cff', '#3955bc'],
    gradient: ['#6a7588', '#9aa6b8', '#c0c8d4', '#7f8cb0'],
    glowA: ['#9aa6b8', '#5a6a82'], glowB: ['#c0c8d4', '#7a86a0'],
  }),
  scheme('mono', 'Mono', {
    primary: ['#d4d4d4', '#3a3a3a'], primaryEmphasis: ['#f0f0f0', '#242424'],
    onPrimary: ['#171717', '#ffffff'], primaryContainer: ['#333333', '#e4e4e4'],
    secondary: ['#a0a0a0', '#5a5a5a'], info: ['#a0a0a0', '#5a5a5a'],
    gradient: ['#8a8a8a', '#d4d4d4', '#f0f0f0', '#a0a0a0'],
    glowA: ['#d4d4d4', '#5a5a5a'], glowB: ['#f0f0f0', '#8a8a8a'],
  }),
  scheme('phosphor', 'Phosphor', {
    primary: ['#3ddc74', '#1a7f3c'], primaryEmphasis: ['#7bf5a5', '#136230'],
    onPrimary: ['#062211', '#ffffff'], primaryContainer: ['#0f3d22', '#d4f7e0'],
    secondary: ['#8ce6a8', '#2b8b52'], info: ['#5cd6c0', '#00695b'],
    gradient: ['#0f6b38', '#3ddc74', '#7bf5a5', '#2b8b52'],
    glowA: ['#3ddc74', '#1a7f3c'], glowB: ['#7bf5a5', '#2b8b52'],
  }),
]

export const DEFAULT_SCHEME = 'gideon'
export function getScheme(id: string): Scheme | undefined { return SCHEMES.find((s) => s.id === id) }

export const COLOR_GROUPS = ['Brand', 'Surfaces', 'Content', 'Semantic', 'Glow & gradient']
export const BACKDROP_GROUPS = ['3D surface', 'Motion', 'Elevation & glass']
export const TYPOGRAPHY_GROUPS = ['Typography']
export const LAYOUT_GROUPS = ['Layout', 'Shape']
