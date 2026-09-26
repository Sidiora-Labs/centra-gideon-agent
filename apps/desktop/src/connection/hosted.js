"use strict";

// Hosted access for the desktop shell. Gideon Cloud is one shared address every customer
// signs in to, so it is a single remote row in the endpoint registry — the shell offers
// it, the customer signs in there, and nothing about a fleet or a provider is administered
// from here. The address is deployment configuration: an unconfigured build simply does
// not offer hosted access rather than guessing a host.

const HOSTED_ENDPOINT_ID = "ep_gideoncloud";
const HOSTED_ENDPOINT_LABEL = "Gideon Cloud";
const HOSTED_URL_ENV = "GIDEON_CLOUD_URL";
const { validateHostedOrigin } = require("./hosted-config");
let packagedHostedUrl = "";

function configureHostedEndpoint(config) {
  packagedHostedUrl = config ? validateHostedOrigin(config.hostedUrl) : "";
}

function hostedGatewayUrl(env = process.env) {
  if (packagedHostedUrl) return packagedHostedUrl;
  const text = String(env?.[HOSTED_URL_ENV] ?? "").trim();
  if (!text) return "";
  let url;
  try {
    url = new URL(text);
  } catch {
    return "";
  }
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash) return "";
  return url.origin;
}

function hostedEndpoint(env = process.env) {
  const base = hostedGatewayUrl(env);
  if (!base) return null;
  return { id: HOSTED_ENDPOINT_ID, label: HOSTED_ENDPOINT_LABEL, base_url: base, kind: "remote", device_session_ref: "" };
}

function isHostedEndpoint(endpoint) {
  return Boolean(endpoint) && endpoint.id === HOSTED_ENDPOINT_ID;
}

// Places the hosted row in a registry without disturbing the customer's own gateways or
// their current selection. Re-running it on a later launch refreshes the address only.
function withHostedEndpoint(registry, env = process.env) {
  const endpoints = Array.isArray(registry?.endpoints) ? registry.endpoints : [];
  const hosted = hostedEndpoint(env);
  const others = endpoints.filter((endpoint) => !isHostedEndpoint(endpoint));
  if (!hosted) {
    const active = others.some((endpoint) => endpoint.id === registry?.active) ? registry.active : others[0]?.id ?? "";
    return { active, endpoints: others };
  }
  const next = [...others, hosted];
  const active = next.some((endpoint) => endpoint.id === registry?.active) ? registry.active : next[0]?.id ?? "";
  return { active, endpoints: next };
}

module.exports = {
  HOSTED_ENDPOINT_ID,
  HOSTED_ENDPOINT_LABEL,
  HOSTED_URL_ENV,
  configureHostedEndpoint,
  hostedGatewayUrl,
  hostedEndpoint,
  isHostedEndpoint,
  withHostedEndpoint,
};
