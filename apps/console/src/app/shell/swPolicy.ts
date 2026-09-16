export const SHELL_DOCUMENT = '/'
export const APP_SHELL = [SHELL_DOCUMENT, '/gideon.svg', '/manifest.webmanifest', '/icons/icon.svg', '/icons/icon-192.png', '/icons/icon-512.png', '/icons/gideon-dark.png', '/icons/gideon-light.png', '/icons/gideon-dark-32.png', '/icons/gideon-light-32.png', '/fonts/dm-sans.woff2'] as const
export const CACHEABLE_PREFIXES = ['/assets/', '/fonts/', '/icons/', '/sprites/', '/vendor/'] as const
export type FetchStrategy = 'network-only' | 'network-first' | 'cache-first'
const shellPaths = new Set<string>(APP_SHELL)
export function isApiPath(pathname: string): boolean { return /^\/api(?:\/|$)/.test(pathname) }
export function mayCache(url: URL, origin: string): boolean {
  const localBuildOutput = shellPaths.has(url.pathname) || CACHEABLE_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))
  return url.origin === origin && !isApiPath(url.pathname) && localBuildOutput
}
export function strategyFor(url: URL, origin: string, isNavigation: boolean): FetchStrategy {
  return isNavigation ? 'network-first' : mayCache(url, origin) ? 'cache-first' : 'network-only'
}
