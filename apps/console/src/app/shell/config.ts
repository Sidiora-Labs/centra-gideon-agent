export const APP_NAME = 'Gideon'

type GideonLinks = { documentationBaseUrl?: string; releasesUrl?: string }

function configuredLinks(): GideonLinks {
  return (window as unknown as { __gideon_config?: GideonLinks }).__gideon_config ?? {}
}

export function documentationUrl(path: string): string | undefined {
  const base = configuredLinks().documentationBaseUrl ?? 'https://github.com/Sidiora-Labs/gideon-agent-harness/blob/main/'
  if (!base) return undefined
  try {
    const url = new URL(path.replace(/^\/+/, ''), base.endsWith('/') ? base : `${base}/`)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined
  } catch { return undefined }
}

export function releasesUrl(): string | undefined {
  try {
    const url = new URL(configuredLinks().releasesUrl ?? '')
    return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined
  } catch { return undefined }
}
