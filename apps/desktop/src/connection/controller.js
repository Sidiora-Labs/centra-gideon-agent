"use strict";

const http = require("node:http"), https = require("node:https"), dns = require("node:dns");
const { hostedGatewayUrl } = require("./hosted");
const { TRUST_LOOPBACK, TRUST_REFUSED, parseGatewayUrl, parsePairingUrl, transportPolicy,
  resolveHostTrust, addressFingerprint } = require("./address");
const { activeEndpoint, addEndpoint, endpointScope, clearEndpointState, findEndpoint,
  loadRegistry, newEndpointId, removeEndpoint, saveRegistry, setActive } = require("../storage/endpoint-registry");
const LOCAL_ENDPOINT_ID = "ep_local", LOCAL_ENDPOINT_LABEL = "This computer";
const CONFIRM_KEY = "connect.confirmed", LABEL_SOURCE_KEY = "connect.labelSource";
const LABEL_MAX = 64, CONFIRM_VERSION = 1, HEALTH_PATH = "/api/healthz", PROBE_TIMEOUT_MS = 4000;
const BACKOFF_BASE_MS = 250, BACKOFF_CEILING = 6, MAX_RECONNECT_ATTEMPTS = 8;
const HEALTH_UNKNOWN = "unknown", HEALTH_REACHABLE = "reachable", HEALTH_UNREACHABLE = "unreachable";
const HEALTH_TIMEOUT = "timeout", HEALTH_NEEDS_PAIRING = "needs_pairing", HEALTH_NOT_A_GATEWAY = "not_a_gateway";
const HEALTH_HTTP_ERROR = "http_error", HEALTH_REDIRECTED = "redirected", HEALTH_REFUSED_BY_POLICY = "refused_by_policy";
const HEALTH_STATES = Object.freeze([HEALTH_UNKNOWN, HEALTH_REACHABLE, HEALTH_UNREACHABLE, HEALTH_TIMEOUT,
  HEALTH_NEEDS_PAIRING, HEALTH_NOT_A_GATEWAY, HEALTH_HTTP_ERROR, HEALTH_REDIRECTED, HEALTH_REFUSED_BY_POLICY]);
const RETRYABLE = Object.freeze([HEALTH_UNREACHABLE, HEALTH_TIMEOUT, HEALTH_HTTP_ERROR]);
const local = (row) => row.id === LOCAL_ENDPOINT_ID || row.kind === "local";

function shouldAttachBridge(url) {
  const target = parseGatewayUrl(String(url || ""));
  return target.ok && target.trust === TRUST_LOOPBACK;
}

function assertLoopbackTarget(url, what = "request") {
  if (shouldAttachBridge(url)) return true;
  throw new Error(`refusing to send ${what} to a non-loopback target (${String(url)}): this call carries a machine-local credential and is only ever valid against the gateway this shell spawned`);
}

function readConfirmation(store, id) {
  const encoded = endpointScope(store, id).get(CONFIRM_KEY);
  if (encoded == null || encoded === "") return { present: false, reason: "absent" };
  let data;
  try { data = JSON.parse(encoded); }
  catch { return { present: false, reason: "unparseable" }; }
  if (!data || typeof data !== "object" || data.v !== CONFIRM_VERSION) return { present: false, reason: "wrong_version" };
  const fields = Object.fromEntries(["origin", "trust", "scheme", "addresses"].map((key) =>
    [key, typeof data[key] === "string" ? data[key] : ""]));
  return { present: true, reason: "", ...fields, at: typeof data.at === "number" ? data.at : 0 };
}

function writeConfirmation(store, id, { origin, trust, scheme, addresses, now = Date.now() }) {
  const confirmation = { v: CONFIRM_VERSION, origin, trust, scheme, addresses: addresses || "", at: Math.floor(now / 1000) };
  endpointScope(store, id).set(CONFIRM_KEY, JSON.stringify(confirmation));
}

function confirmationHolds(record, { origin, currentFingerprint }) {
  const reason = !record.present ? `unconfirmed:${record.reason}` : record.origin !== origin ? "origin_changed"
    : record.addresses && currentFingerprint && record.addresses !== currentFingerprint ? "host_moved" : "";
  return { ok: !reason, reason };
}

function describeStartup({ store, registry, now = Date.now(), currentFingerprint = "" } = {}) {
  const saved = registry || loadRegistry(store), warnings = [];
  const fallback = (reason, warning) => {
    if (warning) warnings.push(warning);
    return { mode: "spawn-local", endpointId: LOCAL_ENDPOINT_ID, reason, warnings };
  };
  if (store?.permissions?.exists && !store.permissions.safe) return fallback("store_not_owner_only", {
    code: "store_permissions", detail: store.permissions.reason,
    message: "Your saved gateway list is readable or writable by more than your own account, so the app will ask you to confirm a gateway again before connecting to it.",
  });
  const invalidStorage = {
    unparseable: "Your saved gateway list could not be read. It has been left exactly as it is — nothing was overwritten — and the app started its own local gateway instead.",
    unreadable: "Your saved gateway list could not be opened. The app started its own local gateway instead.",
  }[store?.status];
  if (invalidStorage) return fallback(`store_${store.status}`, { code: `store_${store.status}`, detail: store.storeReason, message: invalidStorage });
  const endpoint = activeEndpoint(saved);
  if (!endpoint) return fallback(saved.endpoints.length ? "no_active_endpoint" : !store || store.status === "absent" ? "no_saved_endpoints" : "registry_empty");
  if (local(endpoint)) return fallback("active_is_local");
  if (!endpoint.base_url) return fallback("active_endpoint_has_no_url");
  const address = parseGatewayUrl(endpoint.base_url);
  if (!address.ok) return fallback("active_endpoint_unparseable", {
    code: "active_endpoint_unusable", detail: address.code,
    message: `The saved gateway "${endpoint.label || endpoint.base_url}" has an address the app will not use.`,
  });
  const policy = transportPolicy(address);
  if (!policy.allowed) return fallback("active_endpoint_refused_by_policy", {
    code: "active_endpoint_refused", detail: policy.code, message: policy.warning || `The app will not connect to ${address.host}.`,
  });
  const confirmation = confirmationHolds(readConfirmation(store, endpoint.id), { origin: address.origin, currentFingerprint });
  if (!confirmation.ok) return fallback("active_endpoint_unconfirmed", {
    code: "active_endpoint_needs_confirmation", detail: confirmation.reason,
    message: confirmation.reason === "host_moved"
      ? `"${endpoint.label || address.host}" now points at a different machine. Confirm it again before connecting.`
      : `Confirm "${endpoint.label || address.host}" again before connecting.`,
  });
  return { mode: "connect", endpointId: endpoint.id, endpoint, origin: address.origin,
    trust: address.trust, reason: "confirmed_active_endpoint", warnings, at: now };
}

async function prepareEndpoint(input, { lookup = dns.promises.lookup } = {}) {
  const value = String(input ?? "").trim();
  if (!value) return { ok: false, code: "EMPTY", message: "Paste a pairing link or type your gateway's address." };
  const address = /^https?:\/\/[^\s]*\/pair(\?|$)/i.test(value) ? parsePairingUrl(value) : parseGatewayUrl(value);
  if (!address.ok) return address;
  const resolution = await resolveHostTrust(address.hostname, { lookup });
  if (!resolution.ok || resolution.effective === TRUST_REFUSED) {
    const explanations = {
      dns_failed: "could not be looked up. Check the name, or use the IP address.",
      dns_empty: "has no addresses.", mixed_resolution: "answers with a mix of private and public addresses, so the app cannot tell you which network it is on.",
      resolves_to_refused_range: "points at an address range the app will not connect to.",
    };
    return { ok: false, code: `RESOLUTION:${resolution.reason}`, addresses: resolution.addresses,
      message: `${address.hostname} ${explanations[resolution.reason] || "could not be resolved to something to connect to."}` };
  }
  const policy = transportPolicy({ scheme: address.scheme, trust: resolution.effective });
  if (!policy.allowed) return { ok: false, code: policy.code, message: policy.warning, trust: resolution.effective, host: address.host };
  return { ok: true, origin: address.origin, scheme: address.scheme, host: address.host, hostname: address.hostname,
    literalTrust: address.trust, trust: resolution.effective, resolvedTrust: resolution.resolved,
    resolutionReason: resolution.reason, addresses: resolution.addresses, fingerprint: addressFingerprint(resolution.addresses),
    policy, schemeAssumed: Boolean(address.schemeAssumed), pairingCode: address.pairingCode || "", pairTarget: address.pairTarget || "",
    navigateTo: address.pairTarget || address.origin };
}

function confirmEndpoint(store, plan, { label = "", id, kind = "remote", now = Date.now(), mintId = newEndpointId } = {}) {
  const saved = loadRegistry(store);
  const existing = id ? findEndpoint(saved, id) : saved.endpoints.find((row) => row.base_url === plan.origin && row.id !== LOCAL_ENDPOINT_ID);
  const identity = existing?.id || id || mintId();
  const chosen = sanitizeLabel(label);
  const registry = addEndpoint(saved, { id: identity, label: chosen || existing?.label || plan.host,
    base_url: plan.origin, kind, device_session_ref: existing?.device_session_ref || "" });
  writeConfirmation(store, identity, { origin: plan.origin, trust: plan.trust, scheme: plan.scheme, addresses: plan.fingerprint, now });
  if (chosen) endpointScope(store, identity).set(LABEL_SOURCE_KEY, "user");
  saveRegistry(store, registry);
  return { registry, id: identity };
}

function sanitizeLabel(text) {
  const characters = Array.from(String(text ?? ""), (character) => {
    const value = character.codePointAt(0);
    return [9, 10, 13].includes(value) ? " " : value < 32 || value === 127 ? "" : character;
  });
  return characters.join("").replace(/\s+/g, " ").trim().slice(0, LABEL_MAX);
}

function saveRow(store, registry, row) {
  const updated = addEndpoint(registry, row);
  const next = registry.active ? setActive(updated, registry.active) : updated;
  saveRegistry(store, next);
  return next;
}

function adoptGatewayLabel(store, id, instanceName) {
  const label = sanitizeLabel(instanceName);
  if (!label) return { changed: false, reason: "empty_name" };
  if (endpointScope(store, id).get(LABEL_SOURCE_KEY) === "user") return { changed: false, reason: "user_named_it" };
  const registry = loadRegistry(store), endpoint = findEndpoint(registry, id);
  if (!endpoint) return { changed: false, reason: "unknown_endpoint" };
  if (endpoint.label === label) return { changed: false, reason: "already_current" };
  saveRow(store, registry, { ...endpoint, label });
  return { changed: true, label };
}

function rememberLocalGateway(store, baseUrl, { label = LOCAL_ENDPOINT_LABEL } = {}) {
  return saveRow(store, loadRegistry(store), { id: LOCAL_ENDPOINT_ID, label, base_url: String(baseUrl || ""), kind: "local", device_session_ref: "" });
}

function forgetEndpoint(store, id) {
  if (id === LOCAL_ENDPOINT_ID) return { ok: false, reason: "local_endpoint_is_not_removable" };
  const registry = loadRegistry(store);
  if (!findEndpoint(registry, id)) return { ok: false, reason: "unknown_endpoint" };
  const next = removeEndpoint(registry, id);
  clearEndpointState(store, id);
  saveRegistry(store, next);
  return { ok: true, registry: next };
}

function switchTo(store, id, { currentFingerprint = "", localBaseUrl = "" } = {}) {
  const saved = loadRegistry(store), endpoint = findEndpoint(saved, id);
  if (!endpoint) return { ok: false, reason: "unknown_endpoint" };
  let destination;
  if (local(endpoint)) {
    destination = localBaseUrl || endpoint.base_url;
    if (!destination) return { ok: false, reason: "local_gateway_not_running" };
  } else {
    const address = parseGatewayUrl(endpoint.base_url);
    if (!address.ok) return { ok: false, reason: `unusable_url:${address.code}` };
    const policy = transportPolicy(address);
    if (!policy.allowed) return { ok: false, reason: `refused_by_policy:${policy.code}` };
    const held = confirmationHolds(readConfirmation(store, id), { origin: address.origin, currentFingerprint });
    if (!held.ok) return { ok: false, reason: `needs_confirmation:${held.reason}`, needsConfirmation: true, endpoint };
    destination = address.origin;
  }
  const registry = setActive(saved, id);
  saveRegistry(store, registry);
  return { ok: true, registry, endpoint, navigateTo: destination, attachBridge: shouldAttachBridge(destination), needsConfirmation: false };
}

function probeEndpoint(baseUrl, { timeoutMs = PROBE_TIMEOUT_MS, httpMod = http, httpsMod = https } = {}) {
  const payload = (status, httpStatus = 0, detail = "", version = "") => ({ status, httpStatus, version, detail });
  const address = parseGatewayUrl(String(baseUrl || ""));
  const policy = address.ok ? transportPolicy(address) : null;
  if (!address.ok || !policy.allowed) return Promise.resolve(payload(HEALTH_REFUSED_BY_POLICY, 0, address.code || policy.code));
  return new Promise((resolve) => {
    let settled = false;
    const done = (...args) => { if (!settled) { settled = true; resolve(payload(...args)); } };
    const hosted = address.origin === hostedGatewayUrl();
    const target = new URL(hosted ? "/gideon/v1/healthz" : HEALTH_PATH, address.origin);
    const transport = target.protocol === "https:" ? httpsMod : httpMod;
    const request = transport.get({ protocol: target.protocol, hostname: target.hostname,
      port: target.port || (target.protocol === "https:" ? 443 : 80), path: target.pathname,
      timeout: timeoutMs, agent: false, headers: { Accept: "application/json", "User-Agent": "Gideon-Desktop-Probe" } },
      (response) => {
        const status = response.statusCode || 0;
        if (status >= 300 && status < 400) { response.resume(); return done(HEALTH_REDIRECTED, status, "not_followed"); }
        if ([401, 403].includes(status)) { response.resume(); return done(HEALTH_NEEDS_PAIRING, status); }
        let content = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => { content += chunk.slice(0, Math.max(0, 4096 - content.length)); });
        response.on("error", (error) => done(HEALTH_UNREACHABLE, status, String(error?.code || "stream_error")));
        response.on("end", () => {
          if (status < 200 || status >= 300) return done(HEALTH_HTTP_ERROR, status, content.slice(0, 120));
          let data;
          try { data = JSON.parse(content); }
          catch { return done(HEALTH_NOT_A_GATEWAY, status, "body_not_json"); }
          if (data?.status !== "ok" || (hosted ? data.role !== "central" : typeof data.version !== "string")) return done(HEALTH_NOT_A_GATEWAY, status, "unexpected_shape");
          done(HEALTH_REACHABLE, status, "", data.version || "hosted");
        });
      });
    request.on("timeout", () => { done(HEALTH_TIMEOUT, 0, `${timeoutMs}ms`); request.destroy(); });
    request.on("error", (error) => done(HEALTH_UNREACHABLE, 0, String(error?.code || "")));
  });
}

const isRetryable = (status) => RETRYABLE.includes(status);

function nextReconnectStep({ status, attempt = 0, maxAttempts = MAX_RECONNECT_ATTEMPTS } = {}) {
  const result = (action, reason, delayMs = 0, count = attempt) => ({ action, delayMs, attempt: count, reason });
  if (status === HEALTH_REACHABLE) return result("stay", "reachable", 0, 0);
  if (status === HEALTH_NEEDS_PAIRING) return result("needs_pairing", "auth_refused_terminal");
  if (!isRetryable(status)) return result("stop", `terminal:${status}`);
  if (attempt + 1 > maxAttempts) return result("give_up", "attempts_exhausted");
  return result("retry", `retryable:${status}`, BACKOFF_BASE_MS * 2 ** Math.min(attempt + 1, BACKOFF_CEILING), attempt + 1);
}

async function probeAll(registry, { probe = probeEndpoint, localBaseUrl = "" } = {}) {
  const jobs = (registry.endpoints || []).map(async (endpoint) => {
    const target = local(endpoint) ? localBaseUrl || endpoint.base_url : endpoint.base_url;
    const health = target ? await probe(target) : { status: HEALTH_UNKNOWN, httpStatus: 0, version: "", detail: "no_url" };
    return [endpoint.id, health];
  });
  return Object.fromEntries(await Promise.all(jobs));
}

async function currentFingerprintFor(baseUrl, { lookup = dns.promises.lookup } = {}) {
  const address = parseGatewayUrl(String(baseUrl || ""));
  if (!address.ok) return "";
  const answer = await resolveHostTrust(address.hostname, { lookup });
  return answer.ok ? addressFingerprint(answer.addresses) : "";
}

module.exports = {
  LOCAL_ENDPOINT_ID,
  LOCAL_ENDPOINT_LABEL,
  CONFIRM_KEY,
  CONFIRM_VERSION,
  LABEL_SOURCE_KEY,
  LABEL_MAX,
  HEALTH_PATH,
  PROBE_TIMEOUT_MS,
  BACKOFF_BASE_MS,
  BACKOFF_CEILING,
  MAX_RECONNECT_ATTEMPTS,
  HEALTH_UNKNOWN,
  HEALTH_REACHABLE,
  HEALTH_UNREACHABLE,
  HEALTH_TIMEOUT,
  HEALTH_NEEDS_PAIRING,
  HEALTH_NOT_A_GATEWAY,
  HEALTH_HTTP_ERROR,
  HEALTH_REDIRECTED,
  HEALTH_REFUSED_BY_POLICY,
  HEALTH_STATES,
  RETRYABLE,
  assertLoopbackTarget,
  shouldAttachBridge,
  readConfirmation,
  writeConfirmation,
  confirmationHolds,
  describeStartup,
  prepareEndpoint,
  currentFingerprintFor,
  confirmEndpoint,
  sanitizeLabel,
  adoptGatewayLabel,
  rememberLocalGateway,
  forgetEndpoint,
  switchTo,
  probeEndpoint,
  isRetryable,
  nextReconnectStep,
  probeAll,
};
