
export const brandSpectrum =
  'linear-gradient(135deg, #c85a48, #ff6b5b, #ff9a7a, #ffb454, #ff6b5b, #c85a48)'

export function directional(angle = 100): string {
  return [
    `linear-gradient(${angle}deg,`,
    '#ff6b5b 0%, #ff6b5b 12%,',
    '#ff835f 40%,',
    'rgba(255,154,122,0.5) 70%,',
    'transparent 100%)',
  ].join(' ')
}

export function bloom(): string {
  return [
    'radial-gradient(60% 50% at 50% 58%, rgba(255,107,91,0.22) 0%, transparent 70%),',
    'radial-gradient(40% 40% at 62% 70%, rgba(255,154,122,0.14) 0%, transparent 72%),',
    'radial-gradient(50% 45% at 40% 72%, rgba(255,180,84,0.10) 0%, transparent 70%)',
  ].join(' ')
}

export const ringGradient = 'conic-gradient(from var(--angle,0deg), #ffb09f, #fff5f2, #ff6b5b, #ffb454, #ffb09f)'

export function thinkingGlow(): string {
  return [
    'radial-gradient(60% 80% at 30% 0%, rgba(255,107,91,0.35) 0%, transparent 60%),',
    'radial-gradient(50% 70% at 70% 0%, rgba(255,154,122,0.30) 0%, transparent 60%),',
    'radial-gradient(80% 60% at 50% 0%, rgba(255,180,84,0.22) 0%, transparent 70%)',
  ].join(' ')
}
