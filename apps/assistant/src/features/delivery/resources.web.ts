import { gatewayPath, gatewayResource, gatewayResourceHref, gatewayWebSocketUrl, openGatewayEventSource } from '../../shared/transport.web'

function segment(value: string, name: string): string {
  if (!value || value.length > 512 || value === '.' || value === '..' || /[\u0000-\u001f\u007f]/.test(value)) {
    throw new TypeError(`Invalid ${name}`)
  }
  return encodeURIComponent(value)
}

export function artifactHref(slug: string, raw = false): string {
  return gatewayResourceHref(`/api/artifacts/${segment(slug, 'artifact ID')}${raw ? '/raw' : ''}`)
}

export function outboxDownloadHref(filename: string): string {
  if (filename.includes('/') || filename.includes('\\')) throw new TypeError('Invalid download filename')
  return gatewayResourceHref(`/api/outbox/${segment(filename, 'download filename')}`)
}

export function ownedResourceHref(path: string): string {
  return gatewayResourceHref(path)
}

export function openOwnedResource(path: string, signal?: AbortSignal): Promise<Response> {
  return gatewayResource(path, signal)
}

export function ownedEventStream(path: string): EventSource {
  return openGatewayEventSource(path)
}

export function ownedWebSocketUrl(path = '/api/ws'): string {
  return gatewayWebSocketUrl(path)
}

export function ownedResourcePath(path: string): string {
  return gatewayPath(path)
}
