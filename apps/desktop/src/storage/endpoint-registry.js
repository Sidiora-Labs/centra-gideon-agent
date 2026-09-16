"use strict";

const REGISTRY_FIELDS = Object.freeze(["active", "endpoints"]);
const ENDPOINT_FIELDS = Object.freeze(["id", "label", "base_url", "kind", "device_session_ref"]);
const REGISTRY_STORAGE_KEY = "companion:endpoints";
const EMPTY_REGISTRY = Object.freeze({ active: "", endpoints: [] });
const ENDPOINT_KEY_PREFIX = "ep:";
const WS_PATH = "/api/ws";
const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
const empty = () => ({ active: "", endpoints: [] });
const string = (value) => typeof value === "string" ? value : "";
const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

function randomUnit() {
  if (!globalThis.crypto?.getRandomValues) return Math.random();
  return globalThis.crypto.getRandomValues(new Uint32Array(1))[0] / 4294967296;
}

function newEndpointId(rand = randomUnit) {
  return "ep_" + Array.from({ length: 12 }, () => alphabet[Math.floor(rand() * alphabet.length) % alphabet.length]).join("");
}

function normalizeRegistry(value) {
  if (!record(value)) return empty();
  const unique = new Map();
  for (const candidate of Array.isArray(value.endpoints) ? value.endpoints : []) {
    if (!record(candidate) || !string(candidate.id) || unique.has(candidate.id)) continue;
    unique.set(candidate.id, {
      id: candidate.id, label: string(candidate.label), base_url: string(candidate.base_url),
      kind: candidate.kind === "local" ? "local" : "remote", device_session_ref: string(candidate.device_session_ref),
    });
  }
  const endpoints = Array.from(unique.values());
  return { active: unique.has(value.active) ? value.active : endpoints[0]?.id || "", endpoints };
}

function parseRegistry(raw) {
  try { return normalizeRegistry(JSON.parse(raw)); }
  catch { return empty(); }
}

const serializeRegistry = (registry) => JSON.stringify(registry);
const findEndpoint = (registry, id) => registry.endpoints.find((endpoint) => endpoint.id === id);
const activeEndpoint = (registry) => findEndpoint(registry, registry.active);

function addEndpoint(registry, entry) {
  const id = entry.id === undefined ? newEndpointId() : entry.id;
  const row = Object.fromEntries(ENDPOINT_FIELDS.map((field) => [field, field === "id" ? id : entry[field]]));
  const endpoints = registry.endpoints.slice();
  const position = endpoints.findIndex((endpoint) => endpoint.id === id);
  endpoints.splice(position < 0 ? endpoints.length : position, position < 0 ? 0 : 1, row);
  return { active: id, endpoints };
}

function removeEndpoint(registry, id) {
  if (!findEndpoint(registry, id)) return registry;
  const endpoints = registry.endpoints.filter((endpoint) => endpoint.id !== id);
  return { active: registry.active === id ? endpoints[0]?.id || "" : registry.active, endpoints };
}

function setActive(registry, id) {
  return findEndpoint(registry, id) ? { active: id, endpoints: registry.endpoints } : registry;
}

function endpointKey(id, logicalKey) { return `${ENDPOINT_KEY_PREFIX}${id.length}:${id}:${logicalKey}`; }

function parseEndpointKey(key) {
  const header = /^ep:(\d+):/.exec(key);
  if (!header) return undefined;
  const boundary = header[0].length + Number(header[1]);
  if (key[boundary] !== ":") return undefined;
  return { id: key.slice(header[0].length, boundary), logicalKey: key.slice(boundary + 1) };
}

function scopedKeys(store, id) {
  return Array.from({ length: store.length }, (_, index) => store.key(index))
    .filter((key) => key != null)
    .map((key) => ({ key, decoded: parseEndpointKey(key) }))
    .filter(({ decoded }) => decoded?.id === id);
}

function clearEndpointState(store, id) {
  scopedKeys(store, id).forEach(({ key }) => store.removeItem(key));
}

function endpointScope(store, id) {
  const keyFor = (logicalKey) => endpointKey(id, logicalKey);
  return {
    id,
    get(logicalKey) { return store.getItem(keyFor(logicalKey)); },
    set(logicalKey, value) { store.setItem(keyFor(logicalKey), value); },
    remove(logicalKey) { store.removeItem(keyFor(logicalKey)); },
    logicalKeys() { return scopedKeys(store, id).map(({ decoded }) => decoded.logicalKey); },
    clear() { clearEndpointState(store, id); },
  };
}

function loadRegistry(store, key = REGISTRY_STORAGE_KEY) {
  try { return parseRegistry(store.getItem(key)); }
  catch { return empty(); }
}

function saveRegistry(store, registry, key = REGISTRY_STORAGE_KEY) { store.setItem(key, serializeRegistry(registry)); }

function endpointSocketUrl(baseUrl, route = WS_PATH) {
  try {
    const address = new URL(String(baseUrl ?? "").trim());
    const protocol = { "https:": "wss:", "http:": "ws:" }[address.protocol];
    if (!protocol || !address.host) return undefined;
    return protocol + "//" + address.host + (route.startsWith("/") ? route : "/" + route);
  } catch { return undefined; }
}

const endpointSocket = (endpoint, route = WS_PATH) => endpoint ? endpointSocketUrl(endpoint.base_url, route) : undefined;

module.exports = {
  REGISTRY_FIELDS, ENDPOINT_FIELDS, REGISTRY_STORAGE_KEY, EMPTY_REGISTRY, ENDPOINT_KEY_PREFIX, WS_PATH,
  newEndpointId, normalizeRegistry, parseRegistry, serializeRegistry, findEndpoint, activeEndpoint,
  addEndpoint, removeEndpoint, setActive, endpointKey, parseEndpointKey, endpointScope,
  clearEndpointState, loadRegistry, saveRegistry, endpointSocketUrl, endpointSocket,
};
