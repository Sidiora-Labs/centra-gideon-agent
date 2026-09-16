/** Multi-gateway endpoint registry + per-endpoint storage namespacing (COMPANION-APPS T3.1/T3.3).
 *
 * A companion shell (desktop today, mobile next) holds N *paired gateways* — N independent
 * Gideon brains — and a switcher that re-points `active` and reloads the same served SPA
 * from the new origin. This module owns the registry shape and, more importantly, the one
 * mechanism that keeps two brains from bleeding into each other.
 *
 * 🔑 WHY THIS LIVES IN THE SHELL'S STORAGE SCOPE, NOT THE SPA'S. The served SPA cannot hold this
 * registry, for two independently sufficient reasons, both measured rather than assumed:
 *
 *   1. The SPA is re-downloaded from whichever gateway is active. `apps/desktop/src/application/endpoint-session.js:159` does
 *      `page.loadURL(this.gateway.url)` and `connectMode`'s `navigateToEndpoint` does the same for a
 *      paired gateway's origin — either way the shell loads the dashboard *from that gateway's own
 *      origin*. A registry of N gateways has nowhere to live in a bundle that is itself one of the N.
 *      (Since `CA-8` the shell holds TWO url variables, not one: `localGatewayUrl`
 *      (`apps/desktop/src/application/local-gateway.js:49`, resolved from the spawned gateway's READY line) and `activeUrl`, what
 *      the WebView is actually pointed at. Every credential-bearing call is bound to the first;
 *      only the second ever becomes a gateway this shell did not spawn.)
 *   2. The SPA's storage is ALREADY partitioned, for free, by browser origin. `grep -n partition`
 *      over `desktop/main.js` finds nothing, so the default session partition applies and
 *      per-origin isolation holds. Nothing in the SPA reaches across gateways either:
 *      `useChatSocket.ts:32` opens `${proto}://${location.host}/api/ws` — origin-relative — and
 *      `lib/api.ts` uses relative URLs only (it has no `base_url`/`API_BASE` at all).
 *
 * So the ONLY storage scope that spans all N gateways is the shell's own. That is precisely where
 * bleed is possible, and therefore the only place namespacing is load-bearing. This module is what
 * desktop (T4.1) and mobile import so that neither re-decides the key format — two shells that
 * disagree about the format are two shells that cannot share a registry.
 *
 * Desktop's consumer is `desktop/endpointRegistry.js`, a PORT rather than an import: `desktop/` is
 * plain CommonJS with no build step, so it cannot load this `.ts`. It is held to this file by a
 * DIFFERENTIAL rail — `desktop/test/endpointRegistry.test.js` imports this module for real (Node
 * strips the types) and requires byte-identical output from both implementations over a shared
 * vector table, so a behavioural divergence reds even when the two sides' spellings agree.
 *
 * 🚫 NO HUB, NO GATEWAY-TO-GATEWAY. N endpoints are N client-side rows. Gateways never learn about
 * each other; the client fans out. (The multi-instance hub is permanently vetoed.)
 */

export type EndpointKind = 'local' | 'remote'

export interface CompanionEndpoint {
  id: string
  label: string
  base_url: string
  kind: EndpointKind
  device_session_ref: string
}

export interface EndpointRegistry {
  active: string
  endpoints: CompanionEndpoint[]
}

export const EMPTY_REGISTRY: EndpointRegistry = { active: '', endpoints: [] }

export const REGISTRY_STORAGE_KEY = 'companion:endpoints'


const ID_ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789'

/** Mint an endpoint id.
 *
 * 🪤 THE ID IS NOT DERIVED FROM `base_url`, AND MUST NOT BE. Two rows can legitimately share a
 * host (two gateways behind one reverse proxy, or `localhost` on two ports at different times),
 * so a URL is not unique. And a URL *changes* — a laptop moves networks, a port is reassigned,
 * `gideon.local` becomes a Tailscale name — without the gateway becoming a different brain. If the
 * id were the URL, every such change would silently orphan that endpoint's namespaced state and
 * present a re-paired gateway as a stranger. The id is minted once at pair time and never
 * recomputed; `base_url` is mutable data hanging off it.
 *
 * The alphabet is `[a-z0-9]` so a minted id never needs escaping in a storage key. (The key
 * encoder in `endpointKey` is nonetheless total over arbitrary ids — a registry can be
 * hand-edited, or written by an older shell.)
 */
export function newEndpointId(rand: () => number = defaultRand): string {
  let out = 'ep_'
  for (let i = 0; i < 12; i++) out += ID_ALPHABET[Math.floor(rand() * ID_ALPHABET.length) % ID_ALPHABET.length]
  return out
}

function defaultRand(): number {
  const c = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined
  if (c && typeof c.getRandomValues === 'function') {
    const buf = new Uint32Array(1)
    c.getRandomValues(buf)
    return buf[0] / 0x1_0000_0000
  }
  return Math.random()
}


function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined
}

function coerceEndpoint(v: unknown): CompanionEndpoint | undefined {
  if (!isRecord(v)) return undefined
  const id = str(v.id)
  if (!id) return undefined
  return {
    id,
    label: str(v.label) ?? '',
    base_url: str(v.base_url) ?? '',
    kind: v.kind === 'local' ? 'local' : 'remote',
    device_session_ref: str(v.device_session_ref) ?? '',
  }
}

export function normalizeRegistry(value: unknown): EndpointRegistry {
  if (!isRecord(value)) return EMPTY_REGISTRY
  const rawList = Array.isArray(value.endpoints) ? value.endpoints : []
  const endpoints: CompanionEndpoint[] = []
  const seen = new Set<string>()
  for (const raw of rawList) {
    const ep = coerceEndpoint(raw)
    if (!ep || seen.has(ep.id)) continue
    seen.add(ep.id)
    endpoints.push(ep)
  }
  const wanted = str(value.active) ?? ''
  const active = seen.has(wanted) ? wanted : (endpoints[0]?.id ?? '')
  return { active, endpoints }
}

export function parseRegistry(raw: string | null | undefined): EndpointRegistry {
  if (raw == null || raw === '') return EMPTY_REGISTRY
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return EMPTY_REGISTRY
  }
  return normalizeRegistry(parsed)
}

export function serializeRegistry(reg: EndpointRegistry): string {
  return JSON.stringify(reg)
}


export function findEndpoint(reg: EndpointRegistry, id: string): CompanionEndpoint | undefined {
  return reg.endpoints.find((e) => e.id === id)
}

export function activeEndpoint(reg: EndpointRegistry): CompanionEndpoint | undefined {
  return findEndpoint(reg, reg.active)
}

export function addEndpoint(
  reg: EndpointRegistry,
  entry: Omit<CompanionEndpoint, 'id'> & { id?: string },
): EndpointRegistry {
  const id = entry.id ?? newEndpointId()
  const row: CompanionEndpoint = {
    id,
    label: entry.label,
    base_url: entry.base_url,
    kind: entry.kind,
    device_session_ref: entry.device_session_ref,
  }
  const at = reg.endpoints.findIndex((e) => e.id === id)
  const endpoints = at >= 0 ? reg.endpoints.map((e, i) => (i === at ? row : e)) : [...reg.endpoints, row]
  return { active: id, endpoints }
}

export function removeEndpoint(reg: EndpointRegistry, id: string): EndpointRegistry {
  const endpoints = reg.endpoints.filter((e) => e.id !== id)
  if (endpoints.length === reg.endpoints.length) return reg
  const active = reg.active === id ? (endpoints[0]?.id ?? '') : reg.active
  return { active, endpoints }
}

export function setActive(reg: EndpointRegistry, id: string): EndpointRegistry {
  if (!reg.endpoints.some((e) => e.id === id)) return reg
  return { ...reg, active: id }
}


export const ENDPOINT_KEY_PREFIX = 'ep:'

export function endpointKey(id: string, logicalKey: string): string {
  return `${ENDPOINT_KEY_PREFIX}${id.length}:${id}:${logicalKey}`
}

export function parseEndpointKey(key: string): { id: string; logicalKey: string } | undefined {
  if (!key.startsWith(ENDPOINT_KEY_PREFIX)) return undefined
  const rest = key.slice(ENDPOINT_KEY_PREFIX.length)
  const colon = rest.indexOf(':')
  if (colon <= 0) return undefined
  const lenText = rest.slice(0, colon)
  if (!/^\d+$/.test(lenText)) return undefined
  const len = Number(lenText)
  const body = rest.slice(colon + 1)
  if (body.length < len + 1 || body[len] !== ':') return undefined
  return { id: body.slice(0, len), logicalKey: body.slice(len + 1) }
}

export interface KeyValueStore {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
  readonly length: number
  key(index: number): string | null
}

function snapshotKeys(store: KeyValueStore): string[] {
  const out: string[] = []
  for (let i = 0; i < store.length; i++) {
    const k = store.key(i)
    if (k != null) out.push(k)
  }
  return out
}

export interface EndpointScope {
  readonly id: string
  get(logicalKey: string): string | null
  set(logicalKey: string, value: string): void
  remove(logicalKey: string): void
  logicalKeys(): string[]
  clear(): void
}

export function endpointScope(store: KeyValueStore, id: string): EndpointScope {
  return {
    id,
    get: (logicalKey) => store.getItem(endpointKey(id, logicalKey)),
    set: (logicalKey, value) => store.setItem(endpointKey(id, logicalKey), value),
    remove: (logicalKey) => store.removeItem(endpointKey(id, logicalKey)),
    logicalKeys: () =>
      snapshotKeys(store)
        .map(parseEndpointKey)
        .filter((p): p is { id: string; logicalKey: string } => p?.id === id)
        .map((p) => p.logicalKey),
    clear: () => clearEndpointState(store, id),
  }
}

export function clearEndpointState(store: KeyValueStore, id: string): void {
  for (const k of snapshotKeys(store)) {
    if (parseEndpointKey(k)?.id === id) store.removeItem(k)
  }
}


export function loadRegistry(store: KeyValueStore, key: string = REGISTRY_STORAGE_KEY): EndpointRegistry {
  let raw: string | null = null
  try {
    raw = store.getItem(key)
  } catch {
    return EMPTY_REGISTRY
  }
  return parseRegistry(raw)
}

export function saveRegistry(
  store: KeyValueStore,
  reg: EndpointRegistry,
  key: string = REGISTRY_STORAGE_KEY,
): void {
  store.setItem(key, serializeRegistry(reg))
}


export const WS_PATH = '/api/ws'

export function endpointSocketUrl(baseUrl: string, path: string = WS_PATH): string | undefined {
  const raw = String(baseUrl ?? '').trim()
  if (!raw) return undefined
  let url: URL
  try {
    url = new URL(raw)
  } catch {
    // No base is supplied on purpose: a bare host like `gideon.local:10000` parses as the `gideon.local:`
    // SCHEME with an opaque path, so guessing a scheme for it would silently invent a protocol.
    return undefined
  }
  const proto = url.protocol === 'https:' ? 'wss:' : url.protocol === 'http:' ? 'ws:' : ''
  if (!proto) return undefined
  if (!url.host) return undefined
  const suffix = path.startsWith('/') ? path : `/${path}`
  return `${proto}//${url.host}${suffix}`
}

export function endpointSocket(
  endpoint: CompanionEndpoint | undefined,
  path: string = WS_PATH,
): string | undefined {
  if (!endpoint) return undefined
  return endpointSocketUrl(endpoint.base_url, path)
}
