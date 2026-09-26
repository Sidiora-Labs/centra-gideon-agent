
export type TokenKind = 'color' | 'scalar' | 'select'

export interface ColorToken {
  kind: 'color'
  varName: string
  label: string
  group: string
  dark: string
  light: string
}

export interface ScalarToken {
  kind: 'scalar'
  varName: string
  label: string
  group: string
  value: number
  min: number
  max: number
  step: number
  unit?: string
  runtimeKey?: 'glow' | 'animSpeed' | 'waveAmount' | 'surfaceAngle' | 'surfaceDistance' | 'dotSize' | 'dotDensity' | 'bounciness' | 'expressiveness' | 'dragElastic' | 'swipeVelocity' | 'swipeDistance'
}

export interface SelectToken {
  kind: 'select'
  varName: string
  label: string
  group: string
  value: string
  options: string[]
  runtimeKey?: 'dotShape' | 'dotPattern'
}

export type Token = ColorToken | ScalarToken | SelectToken

const c = (varName: string, label: string, group: string, dark: string, light: string): ColorToken =>
  ({ kind: 'color', varName, label, group, dark, light })

const s = (
  varName: string, label: string, group: string, value: number,
  min: number, max: number, step: number, unit = '', runtimeKey?: ScalarToken['runtimeKey'],
): ScalarToken => ({ kind: 'scalar', varName, label, group, value, min, max, step, unit, runtimeKey })

const sel = (varName: string, label: string, group: string, value: string, options: string[], runtimeKey?: SelectToken['runtimeKey']): SelectToken =>
  ({ kind: 'select', varName, label, group, value, options, runtimeKey })

export const TOKENS: Token[] = [
  // ── Brand ──
  c('--color-primary', 'Primary (Gideon neutral)', 'Brand', '#d9dbeb', '#343e69'),
  c('--color-primary-emphasis', 'Primary emphasis', 'Brand', '#e4e5f0', '#303a63'),
  c('--color-on-primary', 'On primary', 'Brand', '#1c1d26', '#ffffff'),
  c('--color-primary-container', 'Primary container', 'Brand', '#343546', '#e5e7f0'),
  c('--color-secondary', 'Secondary (neutral)', 'Brand', '#b5b8ce', '#4a527e'),

  c('--color-canvas', 'Canvas', 'Surfaces', '#19191c', '#fcfcfd'),
  c('--color-surface', 'Surface', 'Surfaces', '#202024', '#f5f5f7'),
  c('--color-surface-low', 'Surface low', 'Surfaces', '#202024', '#f5f5f7'),
  c('--color-surface-container', 'Surface container', 'Surfaces', '#202024', '#f5f5f7'),
  c('--color-surface-high', 'Surface high', 'Surfaces', '#29292e', '#ededf0'),
  c('--color-surface-highest', 'Surface highest', 'Surfaces', '#303036', '#e7e7ec'),
  c('--color-rail', 'Nav rail', 'Surfaces', '#111113', '#f3f3f5'),

  c('--color-on-surface', 'Text', 'Content', '#eeeeef', '#202027'),
  c('--color-on-surface-low', 'Text muted', 'Content', '#a2a2ad', '#62626f'),
  c('--color-on-surface-var', 'Text variant', 'Content', '#c7c7d0', '#555561'),
  c('--color-outline', 'Outline', 'Content', '#84848f', '#757582'),
  c('--color-outline-variant', 'Outline subtle', 'Content', '#34343b', '#dddde5'),

  c('--color-ok', 'Success', 'Semantic', '#0ebc5f', '#076e37'),
  c('--color-warn', 'Warning', 'Semantic', '#ff8d41', '#954c19'),
  c('--color-danger', 'Danger', 'Semantic', '#f66c66', '#af2f29'),
  c('--color-info', 'Info', 'Semantic', '#aeb8e6', '#3f5185'),

  c('--grad-1', 'Gradient 1', 'Glow & gradient', '#777d9f', '#777d9f'),
  c('--grad-2', 'Gradient 2', 'Glow & gradient', '#d9dbeb', '#343e69'),
  c('--grad-3', 'Gradient 3', 'Glow & gradient', '#aeb8e6', '#59648f'),
  c('--grad-4', 'Gradient 4', 'Glow & gradient', '#b5b8ce', '#b5b8ce'),
  c('--glow-a', 'Glow color A', 'Glow & gradient', '#d9dbeb', '#343e69'),
  c('--glow-b', 'Glow color B', 'Glow & gradient', '#aeb8e6', '#59648f'),
  c('--ring-stop-2', 'Focus-ring highlight', 'Glow & gradient', '#eeeeef', '#142347'),

  s('--ui-zoom', 'UI zoom', 'Typography', 100, 80, 150, 5, '%'),
  s('--font-scale', 'Font size', 'Typography', 100, 85, 160, 5, '%'),
  sel('--font-family', 'Font family', 'Typography', 'inter', ['dm-sans', 'inter', 'mono', 'system']),

  sel('--ui-density', 'UI density', 'Layout', 'comfortable', ['comfortable', 'dense', 'cli']),
  sel('--session-map-density', 'Session map density', 'Layout', 'comfortable', ['comfortable', 'compact']),

  s('--radius-scale', 'Corner roundness', 'Shape', 1, 0, 2, 0.05),

  sel('--bg-style', 'Background', '3D surface', 'none', ['waves', 'still', 'glow', 'none']),
  s('--surface-angle', 'View angle', '3D surface', 45, 0, 180, 1, '°', 'surfaceAngle'),
  s('--surface-distance', 'View distance', '3D surface', 1, 0.4, 2.2, 0.05, '', 'surfaceDistance'),
  s('--dot-size', 'Dot size', '3D surface', 1, 0.3, 9, 0.1, '', 'dotSize'),
  s('--dot-density', 'Dot density', '3D surface', 1, 0.3, 2, 0.05, '', 'dotDensity'),
  sel('--dot-shape', 'Dot shape', '3D surface', 'claude', ['circle', 'square', 'diamond', 'star', 'sparkle', 'burst', 'claude'], 'dotShape'),
  sel('--dot-pattern', 'Arrangement', '3D surface', 'hex', ['grid', 'diamond', 'hex', 'brick'], 'dotPattern'),

  s('--anim-speed', 'Animation speed', 'Motion', 1, 0, 2.5, 0.05, '', 'animSpeed'),
  s('--wave-amount', 'Wave amount', 'Motion', 1, 0, 2.5, 0.05, '', 'waveAmount'),
  s('--glow', 'Glow amount', 'Motion', 1, 0, 2.5, 0.05, '', 'glow'),
  s('--bounciness', 'Bounciness', 'Motion', 1, 0, 1, 0.05, '', 'bounciness'),
  s('--expressiveness', 'Expressiveness', 'Motion', 0.8, 0, 1, 0.05, '', 'expressiveness'),
  s('--drag-elastic', 'Drag elasticity', 'Motion', 0.9, 0, 1, 0.05, '', 'dragElastic'),
  s('--swipe-dismiss-velocity', 'Swipe flick speed', 'Motion', 500, 100, 1500, 25, 'px/s', 'swipeVelocity'),
  s('--swipe-dismiss-distance', 'Swipe distance', 'Motion', 80, 20, 240, 5, 'px', 'swipeDistance'),

  s('--glass-blur', 'Glass blur', 'Elevation & glass', 16, 0, 40, 1, 'px'),
  s('--glass-alpha', 'Glass opacity', 'Elevation & glass', 0.72, 0.4, 1, 0.02),
  s('--status-pulse-speed', 'Status dot pulse', 'Motion', 2.4, 0.6, 6, 0.1, 's'),
]

export const GROUPS = ['Brand', 'Surfaces', 'Content', 'Semantic', 'Glow & gradient', 'Typography', 'Layout', 'Shape', 'Elevation & glass', '3D surface', 'Motion']
