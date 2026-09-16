"use strict";

const TRUST_LOOPBACK = "loopback", TRUST_PRIVATE = "private", TRUST_PUBLIC = "public", TRUST_REFUSED = "refused";
const TRUST_CLASSES = Object.freeze([TRUST_LOOPBACK, TRUST_PRIVATE, TRUST_PUBLIC, TRUST_REFUSED]);
const ALLOWED_SCHEMES = Object.freeze(["http:", "https:"]);
const PAIR_ROUTE = "/pair";
const verdict = (trust, reason) => ({ trust, reason });
const rejection = (code, message) => ({ ok: false, code, message });

function parseIPv4(text) {
  const octets = String(text).split(".");
  if (octets.length !== 4 || octets.some((part) => !/^(0|[1-9]\d{0,2})$/.test(part) || Number(part) > 255)) return null;
  return octets.map(Number);
}

function isNonCanonicalIpShape(host) {
  const numeric = /^(?:\d+|0x[\da-f]+)$/i;
  const labels = host.split(".");
  return numeric.test(host) || (labels.length >= 2 && labels.length <= 4 && labels.every((label) => numeric.test(label)));
}

function parseIPv6(text) {
  let address = String(text).split("%", 1)[0];
  if (!address.includes(":")) return null;
  let ipv4 = null;
  const lastSeparator = address.lastIndexOf(":");
  const suffix = address.slice(lastSeparator + 1);
  if (suffix.includes(".")) {
    ipv4 = parseIPv4(suffix);
    if (!ipv4) return null;
    address = address.slice(0, lastSeparator + 1) + "0:0";
  }
  const segments = address.split("::");
  if (segments.length > 2) return null;
  const decode = (part) => part ? part.split(":").map((word) => /^[\da-f]{1,4}$/i.test(word) ? Number.parseInt(word, 16) : NaN) : [];
  const left = decode(segments[0]);
  const right = segments.length === 2 ? decode(segments[1]) : [];
  const padding = 8 - left.length - right.length;
  if (padding < 0) return null;
  const words = segments.length === 2 ? left.concat(Array(padding).fill(0), right) : left;
  if (words.length !== 8 || words.some(Number.isNaN)) return null;
  if (ipv4) words.splice(6, 2, ipv4[0] * 256 + ipv4[1], ipv4[2] * 256 + ipv4[3]);
  return words;
}

function classifyHost(rawHost) {
  const host = String(rawHost ?? "").trim().replace(/^\[|\]$/g, "").replace(/\.$/, "").toLowerCase();
  if (!host) return verdict(TRUST_REFUSED, "empty_host");
  if (host.includes(":")) {
    const words = parseIPv6(host);
    if (!words) return verdict(TRUST_REFUSED, "malformed_ipv6");
    const zeroPrefix = (count) => words.slice(0, count).every((word) => word === 0);
    if (zeroPrefix(8)) return verdict(TRUST_REFUSED, "unspecified_address");
    if (zeroPrefix(7) && words[7] === 1) return verdict(TRUST_LOOPBACK, "ipv6_loopback");
    if (zeroPrefix(5) && [0, 65535].includes(words[5])) return verdict(TRUST_REFUSED, "mapped_ipv4");
    if ((words[0] & 0xffc0) === 0xfe80) return verdict(TRUST_REFUSED, "ipv6_link_local");
    if ((words[0] & 0xfe00) === 0xfc00) return verdict(TRUST_PRIVATE, "ipv6_unique_local");
    return verdict(TRUST_PUBLIC, "ipv6_global");
  }
  const octets = parseIPv4(host);
  if (octets) {
    const [first, second] = octets;
    const ranges = [
      [first === 127, TRUST_LOOPBACK, "ipv4_loopback"],
      [first === 0, TRUST_REFUSED, "this_network"],
      [first === 169 && second === 254, TRUST_REFUSED, "ipv4_link_local"],
      [first === 10, TRUST_PRIVATE, "rfc1918_10"],
      [first === 172 && second >= 16 && second <= 31, TRUST_PRIVATE, "rfc1918_172"],
      [first === 192 && second === 168, TRUST_PRIVATE, "rfc1918_192"],
      [first === 100 && second >= 64 && second <= 127, TRUST_PRIVATE, "cgnat_tailscale"],
      [first >= 224, TRUST_REFUSED, "multicast_or_reserved"],
    ];
    const match = ranges.find(([applies]) => applies);
    return match ? verdict(match[1], match[2]) : verdict(TRUST_PUBLIC, "ipv4_global");
  }
  if (isNonCanonicalIpShape(host)) return verdict(TRUST_REFUSED, "non_canonical_ip");
  if (host === "localhost") return verdict(TRUST_LOOPBACK, "localhost");
  if (["localhost.localdomain", "ip6-localhost", "ip6-loopback"].includes(host)) return verdict(TRUST_REFUSED, "alternate_loopback_name");
  if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.?$/.test(host)) return verdict(TRUST_REFUSED, "malformed_hostname");
  if (host === "local" || host.endsWith(".local")) return verdict(TRUST_PRIVATE, "mdns_local");
  if (host.endsWith(".ts.net")) return verdict(TRUST_PRIVATE, "tailscale_magicdns");
  return verdict(TRUST_PUBLIC, "name_unresolved");
}

function refusedAddress(reason) {
  const explanations = {
    non_canonical_ip: "Write an address as a normal dotted IP (10.0.0.4), not as a number or in hex.",
    mapped_ipv4: "That is an IPv4 address written as IPv6. Use the plain IPv4 form.",
    ipv4_link_local: "Link-local addresses are not gateways, and one of them is the cloud metadata service.",
    ipv6_link_local: "Link-local addresses are not gateways, and one of them is the cloud metadata service.",
    alternate_loopback_name: "Use localhost or 127.0.0.1 for a gateway on this machine.",
    unspecified_address: "That address does not name a host.", this_network: "That address does not name a host.",
    multicast_or_reserved: "That address range cannot host a gateway.",
    malformed_ipv6: "That is not a hostname or an IP address.", malformed_hostname: "That is not a hostname or an IP address.",
  };
  return { ...rejection(`REFUSED_HOST:${reason}`, explanations[reason] || "That address is not one the app will connect to."),
    trust: TRUST_REFUSED, trustReason: reason };
}

function parseGatewayUrl(raw) {
  const input = String(raw ?? "").trim();
  if (!input) return rejection("EMPTY", "Enter your gateway address.");
  if (/\s/.test(input) || [...input].some((character) => character.codePointAt(0) < 32 || character.codePointAt(0) === 127)) {
    return rejection("CONTROL_CHARS", "That address contains characters an address cannot contain.");
  }
  const schemeAssumed = !/^[a-z][a-z0-9+.-]*:\/\//i.test(input);
  const candidate = schemeAssumed ? "http://" + input : input;
  let address;
  try { address = new URL(candidate); }
  catch { return rejection("BAD_URL", `${input} is not an address.`); }
  if (!ALLOWED_SCHEMES.includes(address.protocol)) return rejection("BAD_SCHEME", "A gateway is reached over http or https, and nothing else.");
  if (address.username || address.password) return rejection("HAS_CREDENTIALS", "Remove the username and password from the address.");
  if (!address.hostname) return rejection("BAD_URL", `${input} is not an address.`);
  if (address.port && !/^\d{1,5}$/.test(address.port)) return rejection("BAD_PORT", "That port is not a number.");
  if (address.port && (Number(address.port) < 1 || Number(address.port) > 65535)) return rejection("BAD_PORT", "That port is outside 1–65535.");
  const authority = candidate.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "").split(/[/?#]/, 1)[0].split("@").at(-1);
  const writtenHost = authority.startsWith("[") ? authority.slice(0, authority.indexOf("]") + 1) : authority.split(":", 1)[0];
  if (writtenHost && !parseIPv4(writtenHost) && isNonCanonicalIpShape(writtenHost)) return refusedAddress("non_canonical_ip");
  const classified = classifyHost(address.hostname);
  if (classified.trust === TRUST_REFUSED) return refusedAddress(classified.reason);
  return { ok: true, origin: address.origin, scheme: address.protocol, host: address.host,
    hostname: address.hostname.replace(/^\[|\]$/g, "").toLowerCase(), port: address.port,
    trust: classified.trust, trustReason: classified.reason, schemeAssumed };
}

function parsePairingUrl(raw) {
  const input = String(raw ?? "").trim();
  if (!input) return rejection("EMPTY", "Paste the pairing link from Settings → Devices.");
  let link;
  try { link = new URL(input); }
  catch { return rejection("NOT_PAIRING", "That is not a pairing link."); }
  const parsed = parseGatewayUrl(input);
  if (!parsed.ok) return parsed;
  if (link.pathname.replace(/\/+$/, "") !== PAIR_ROUTE) return rejection("NOT_PAIRING", "That link does not point at a gateway's pairing page.");
  const pairingCode = (link.searchParams.get("code") || "").trim();
  if (!pairingCode) return rejection("NO_CODE", "That pairing link carries no code.");
  if (!/^[A-Z0-9]{2,8}(-[A-Z0-9]{2,8}){0,3}$/i.test(pairingCode)) return rejection("BAD_CODE", "That pairing code is not in the expected form.");
  const { schemeAssumed, ...base } = parsed;
  return { ...base, pairingCode, pairTarget: `${parsed.origin}${PAIR_ROUTE}?code=${encodeURIComponent(pairingCode)}` };
}

function transportPolicy({ scheme, trust }) {
  const response = (allowed, requiresConfirmation, code, warning = "") => ({ allowed, requiresConfirmation, code, warning });
  if (trust === TRUST_REFUSED) return response(false, false, "REFUSED_HOST");
  if (!ALLOWED_SCHEMES.includes(scheme)) return response(false, false, "BAD_SCHEME");
  const secure = scheme === "https:";
  if (trust === TRUST_LOOPBACK) return response(true, false, secure ? "LOOPBACK_TLS_OK" : "LOOPBACK_PLAINTEXT_OK");
  if (!secure && trust === TRUST_PUBLIC) return response(false, false, "PLAINTEXT_PUBLIC_REFUSED",
    "This address is on the public internet and http:// would send your session in the clear. Use https:// — a remote gateway is reached through your own tunnel, which terminates TLS.");
  if (!secure) return response(true, true, "PLAINTEXT_PRIVATE_CONFIRM",
    "http:// on a local network is not encrypted. Anyone else on this network can read this session, including the cookie that keeps you signed in. Only continue on a network you trust.");
  return response(true, true, trust === TRUST_PUBLIC ? "TLS_PUBLIC_CONFIRM" : "TLS_PRIVATE_CONFIRM",
    "Check the address below is your own gateway. The app cannot tell your gateway apart from any other machine answering at this address.");
}

async function resolveHostTrust(hostname, { lookup } = {}) {
  const initial = classifyHost(hostname);
  const base = { ok: false, literal: initial.trust, resolved: null, effective: TRUST_REFUSED, addresses: [] };
  if (initial.trust === TRUST_REFUSED) return { ...base, reason: initial.reason };
  const literal = parseIPv4(hostname) !== null || String(hostname).includes(":");
  if (literal || typeof lookup !== "function") return { ...base, ok: true,
    resolved: literal ? initial.trust : null, effective: initial.trust,
    addresses: literal ? [String(hostname)] : [], reason: literal ? initial.reason : "not_resolved" };
  let records;
  try { records = await lookup(hostname, { all: true }); }
  catch (error) { return { ...base, reason: "dns_failed", detail: String(error?.code || "") }; }
  const addresses = [].concat(records).map((value) => value && typeof value === "object" ? value.address : value)
    .filter((value) => typeof value === "string" && value);
  if (!addresses.length) return { ...base, reason: "dns_empty" };
  const classes = Array.from(new Set(addresses.map((address) => classifyHost(address).trust)));
  if (classes.includes(TRUST_REFUSED)) return { ...base, addresses, resolved: TRUST_REFUSED, reason: "resolves_to_refused_range" };
  if (classes.length !== 1) return { ...base, addresses, reason: "mixed_resolution" };
  const resolved = classes[0];
  return { ...base, ok: true, addresses, resolved,
    effective: resolved === TRUST_LOOPBACK ? TRUST_PRIVATE : resolved,
    reason: resolved === TRUST_LOOPBACK ? "name_resolves_to_loopback" : "resolved" };
}

const addressFingerprint = (addresses) => Array.from(new Set((addresses || []).map((address) => String(address).toLowerCase()))).sort().join(",");

module.exports = { TRUST_LOOPBACK, TRUST_PRIVATE, TRUST_PUBLIC, TRUST_REFUSED, TRUST_CLASSES, ALLOWED_SCHEMES, PAIR_ROUTE,
  parseIPv4, parseIPv6, isNonCanonicalIpShape, classifyHost, parseGatewayUrl, parsePairingUrl, transportPolicy,
  resolveHostTrust, addressFingerprint };
