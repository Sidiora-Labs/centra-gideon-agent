export const gideonAssets = {
  dark: { mark: '/icons/gideon-dark.png', favicon: '/icons/gideon-dark-32.png' },
  light: { mark: '/icons/gideon-light.png', favicon: '/icons/gideon-light-32.png' },
} as const

export function attachThemeFavicon(mode: keyof typeof gideonAssets): () => void {
  const identity = document.querySelector<HTMLLinkElement>('link[rel~="icon"]:not([data-gideon-appearance])')
  if (!identity) return () => {}
  const themed = document.createElement('link')
  themed.rel = 'icon'
  themed.type = 'image/png'
  themed.sizes = '32x32'
  themed.dataset.gideonAppearance = mode
  themed.href = gideonAssets[mode].favicon
  const refresh = () => {
    const source = identity.getAttribute('href')?.split(/[?#]/, 1)[0]
    if (source === '/gideon.svg' || source === '/icons/personality-gideon-arcade.svg') {
      document.head.append(themed)
    } else {
      themed.remove()
    }
  }
  const observer = new MutationObserver(refresh)
  observer.observe(identity, { attributes: true, attributeFilter: ['href'] })
  refresh()
  return () => { observer.disconnect(); themed.remove() }
}
