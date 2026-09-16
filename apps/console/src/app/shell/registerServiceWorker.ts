export function serviceWorkerBlockedReason(nav: Navigator = navigator, win: { isSecureContext?: boolean } = window): string | null {
  const requirements: Array<[boolean, string]> = [
    ['serviceWorker' in nav, 'this browser has no service-worker support'],
    [win.isSecureContext !== false, 'the page is not a secure context — reach the gateway over localhost or an https tunnel'],
  ]
  return requirements.find(([available]) => !available)?.[1] ?? null
}
export async function registerServiceWorker(enabled: boolean = import.meta.env.PROD): Promise<ServiceWorkerRegistration | null> {
  if (!enabled) return null
  const reason = serviceWorkerBlockedReason()
  if (reason) { console.info(`Gideon: offline support and install are unavailable — ${reason}.`); return null }
  try { return await navigator.serviceWorker.register('/sw.js', { scope: '/' }) }
  catch (error) {
    console.warn('Gideon: service-worker registration failed', error)
    return null
  }
}
