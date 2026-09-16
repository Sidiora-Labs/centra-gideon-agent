export const COMPANION_ROUTE = '#/companion'
export const PAIR_ROUTE = '/pair'
export const PRIVATE_HOST_PATTERNS = Object.freeze([
  'localhost',
  '127.0.0.1',
  '*.local',
  '10.*',
  '192.168.*',
  '172.16.*',
  '172.17.*',
  '172.18.*',
  '172.19.*',
  '172.20.*',
  '172.21.*',
  '172.22.*',
  '172.23.*',
  '172.24.*',
  '172.25.*',
  '172.26.*',
  '172.27.*',
  '172.28.*',
  '172.29.*',
  '172.30.*',
  '172.31.*',
  '*.ts.net',
])

export class GatewayUrlError extends Error {
  constructor(code, message) {
    super(message)
    this.name = 'GatewayUrlError'
    this.code = code
  }
}

const hostMatchers = new Map()
export function matchesHostPattern(host, pattern) {
  if (!host || !pattern) return false
  if (!hostMatchers.has(pattern)) {
    const source = String(pattern).split('*').map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.*')
    hostMatchers.set(pattern, new RegExp(`^${source}$`, 'i'))
  }
  return hostMatchers.get(pattern).test(String(host))
}

export const isPrivateGatewayHost = (host) => PRIVATE_HOST_PATTERNS.some((pattern) => matchesHostPattern(host, pattern))

function parseAddress(input, { bare = false, empty, invalid }) {
  const text = String(input ?? '').trim()
  if (!text) throw new GatewayUrlError('EMPTY', empty)
  const candidate = bare && !/^[a-z][a-z\d+.-]*:\/\//i.test(text) ? `http://${text}` : text
  try {
    return new URL(candidate)
  } catch {
    throw new GatewayUrlError('BAD_URL', invalid ?? `${text} is not an address.`)
  }
}

function gatewayOrigin(url) {
  const checks = [
    ['BAD_SCHEME', !['http:', 'https:'].includes(url.protocol), 'A gateway is reached over http or https.'],
    ['BAD_URL', !url.hostname, 'Enter a gateway with a hostname.'],
    ['NOT_PRIVATE', !isPrivateGatewayHost(url.hostname), `${url.hostname} is not on your private network. The companion is reached over your LAN or tailnet; a public address has to be added to the shell's allowed hosts.`],
  ]
  const failure = checks.find(([, rejected]) => rejected)
  if (failure) throw new GatewayUrlError(failure[0], failure[2])
  return url.origin
}

export function normalizeGatewayUrl(raw) {
  return gatewayOrigin(parseAddress(raw, { bare: true, empty: 'Enter your gateway address.' }))
}

export const companionUrl = (raw) => `${normalizeGatewayUrl(raw)}/${COMPANION_ROUTE}`

export function pairingTargetFromScan(scanned) {
  const url = parseAddress(scanned, { empty: 'Nothing was scanned.', invalid: 'That QR code is not a pairing link.' })
  const gatewayUrl = gatewayOrigin(url)
  if (url.pathname.replace(/\/+$/, '') !== PAIR_ROUTE) {
    throw new GatewayUrlError('NOT_PAIRING', 'That QR code is not a pairing link.')
  }
  const code = url.searchParams.get('code')?.trim()
  if (!code) throw new GatewayUrlError('NO_CODE', 'That pairing link carries no code.')
  return { gatewayUrl, target: `${gatewayUrl}${PAIR_ROUTE}?code=${encodeURIComponent(code)}` }
}
