export const accentChip = {
  background: 'var(--color-primary-container)',
  color: 'var(--color-on-primary-container)',
} as const

export const mutedChip = {
  background: 'var(--color-surface-high)',
  color: 'var(--color-on-surface-var)',
} as const

export function toneChipSkin(tone: string, strength = 14): { background: string; color: string } {
  if (tone === 'var(--color-primary)') return { ...accentChip }
  return { background: `color-mix(in srgb, ${tone} ${strength}%, transparent)`, color: tone }
}
