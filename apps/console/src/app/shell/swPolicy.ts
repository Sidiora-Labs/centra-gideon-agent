export const SHELL_DOCUMENT = '/'
export const ASSISTANT_DOCUMENT = '/assistant/'
export const APP_SHELL = [SHELL_DOCUMENT, '/gideon.svg', '/manifest.webmanifest', '/icons/icon.svg', '/icons/icon-192.png', '/icons/icon-512.png', '/icons/gideon-dark.png', '/icons/gideon-light.png', '/icons/gideon-dark-32.png', '/icons/gideon-light-32.png', '/fonts/dm-sans.woff2'] as const
export const CACHEABLE_PREFIXES = ['/assets/', '/fonts/', '/icons/', '/sprites/', '/vendor/', '/assistant/_expo/', '/assistant/assets/'] as const
export type FetchStrategy = 'network-only' | 'network-first' | 'cache-first'
const shellPaths = new Set<string>(APP_SHELL)
export function isApiPath(pathname: string): boolean { return /^\/(?:assistant\/)?api(?:\/|$)/.test(pathname) }
export function shellDocument(url: URL, origin: string): string | undefined {
  if (url.origin !== origin || isApiPath(url.pathname)) return undefined
  if (url.pathname === SHELL_DOCUMENT) return SHELL_DOCUMENT
  if (url.pathname === '/assistant' || url.pathname === ASSISTANT_DOCUMENT ||
    (/^\/assistant\/[a-z][a-z0-9/-]*$/i.test(url.pathname) && !url.pathname.startsWith('/assistant/assets/') && !url.pathname.startsWith('/assistant/_expo/'))) return ASSISTANT_DOCUMENT
  return undefined
}
export function mayCache(url: URL, origin: string): boolean {
  const localBuildOutput = shellPaths.has(url.pathname) || url.pathname === ASSISTANT_DOCUMENT || CACHEABLE_PREFIXES.some(prefix => url.pathname.startsWith(prefix))
  return url.origin === origin && !url.search && !isApiPath(url.pathname) && localBuildOutput
}
export function cacheableResponse(url: URL, response: Response, origin: string): boolean {
  if (!mayCache(url, origin) || response.status !== 200 || response.redirected) return false
  const type = (response.headers.get('content-type') ?? '').split(';', 1)[0].trim().toLowerCase()
  if (url.pathname === SHELL_DOCUMENT || url.pathname === ASSISTANT_DOCUMENT) return type === 'text/html'
  if (url.pathname.endsWith('.js')) return type === 'text/javascript' || type === 'application/javascript'
  if (url.pathname.endsWith('.css')) return type === 'text/css'
  if (url.pathname.endsWith('.webmanifest')) return type === 'application/manifest+json'
  return type !== 'text/html' && type !== ''
}
export function strategyFor(url: URL, origin: string, isNavigation: boolean): FetchStrategy {
  if (url.origin !== origin || isApiPath(url.pathname)) return 'network-only'
  if (isNavigation) return shellDocument(url, origin) ? 'network-first' : 'network-only'
  return mayCache(url, origin) ? 'cache-first' : 'network-only'
}
