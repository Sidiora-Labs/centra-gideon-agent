export const REGISTRY_STORAGE_KEY = 'companion:endpoints'
export const REGISTRY_FIELDS = Object.freeze(['active', 'endpoints'])
export const ENDPOINT_FIELDS = Object.freeze(['id', 'label', 'base_url', 'kind', 'device_session_ref'])
export const EMPTY_REGISTRY = Object.freeze({ active: '', endpoints: Object.freeze([]) })

const alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789'
const textField = (value) => typeof value === 'string' ? value : ''
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value)
const fields = {
  id: textField,
  label: textField,
  base_url: textField,
  kind: (value) => value === 'local' ? 'local' : 'remote',
  device_session_ref: textField,
}

function randomFraction() {
  if (!globalThis.crypto?.getRandomValues) return Math.random()
  return globalThis.crypto.getRandomValues(new Uint32Array(1))[0] / 0x1_0000_0000
}

export function newEndpointId(random = randomFraction) {
  return 'ep_' + Array.from({ length: 12 }, () => alphabet[Math.floor(random() * alphabet.length) % alphabet.length]).join('')
}

export function normalizeRegistry(value) {
  const input = isRecord(value) ? value : {}
  const unique = new Map()
  for (const row of Array.isArray(input.endpoints) ? input.endpoints : []) {
    if (!isRecord(row) || !textField(row.id) || unique.has(row.id)) continue
    unique.set(row.id, Object.fromEntries(ENDPOINT_FIELDS.map((field) => [field, fields[field](row[field])])))
  }
  const endpoints = [...unique.values()]
  return { active: unique.has(input.active) ? input.active : endpoints[0]?.id ?? '', endpoints }
}

export function readRegistry(storage) {
  try {
    return normalizeRegistry(JSON.parse(storage?.getItem?.(REGISTRY_STORAGE_KEY) ?? 'null'))
  } catch {
    return normalizeRegistry(null)
  }
}

export function writeRegistry(storage, registry) {
  try {
    storage?.setItem?.(REGISTRY_STORAGE_KEY, JSON.stringify(registry))
    return true
  } catch {
    return false
  }
}

export const activeEndpoint = (registry) => registry?.endpoints?.find(({ id }) => id === registry.active)
export const activeBaseUrl = (registry) => activeEndpoint(registry)?.base_url ?? ''

export function rememberGateway(storage, { baseUrl, label = '', mintId = newEndpointId } = {}) {
  const previous = readRegistry(storage)
  const found = previous.endpoints.find(({ base_url }) => base_url === baseUrl)
  const selected = found
    ? { ...found, label: label || found.label }
    : { id: mintId(), label, base_url: baseUrl, kind: 'remote', device_session_ref: '' }
  const registry = {
    active: selected.id,
    endpoints: found
      ? previous.endpoints.map((row) => row.id === selected.id ? selected : row)
      : [...previous.endpoints, selected],
  }
  return { registry, stored: writeRegistry(storage, registry) }
}

export function forgetActiveGateway(storage) {
  const previous = readRegistry(storage)
  const registry = normalizeRegistry({ endpoints: previous.endpoints.filter(({ id }) => id !== previous.active) })
  writeRegistry(storage, registry)
  return registry
}
