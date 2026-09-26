"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { navigationDecision, hostedOAuthStart } = require("../src/application/window-workspace");
const { configureHostedEndpoint, hostedGatewayUrl, withHostedEndpoint, HOSTED_ENDPOINT_ID } = require("../src/connection/hosted");

const HOSTED = "https://gideon.centra.ag";
const OWNED = { id: "ep_owned", label: "Owned workspace", base_url: "http://localhost:4321", kind: "local", device_session_ref: "" };

function authorize(provider = "github", redirect = `${HOSTED}/?return_to=%23%2Fprojects`) {
  const address = new URL(`${HOSTED}/auth/v1/authorize`);
  address.searchParams.set("provider", provider);
  address.searchParams.set("redirect_to", redirect);
  return address;
}

test("navigation treats the exact hosted origin as internal and externalizes lookalikes and HTTP", () => {
  const canonical = navigationDecision("https://gideon.centra.ag:443/projects?tab=active", HOSTED);
  assert.deepEqual(canonical, { allowed: true, external: "", origin: HOSTED });

  const lookalike = navigationDecision("https://gideon.centra.ag.attacker.test/session", HOSTED);
  assert.deepEqual(lookalike, { allowed: false,
    external: "https://gideon.centra.ag.attacker.test/session",
    origin: "https://gideon.centra.ag.attacker.test" });
  const insecure = navigationDecision("http://gideon.centra.ag/session", HOSTED);
  assert.deepEqual(insecure, { allowed: false, external: "http://gideon.centra.ag/session", origin: "http://gideon.centra.ag" });

  const unconfirmed = navigationDecision(`${HOSTED}/projects`, "");
  assert.equal(unconfirmed.allowed, false);
  assert.equal(unconfirmed.external, `${HOSTED}/projects`);
  assert.deepEqual(navigationDecision("mailto:owner@gideon.centra.ag", HOSTED),
    { allowed: false, external: "", origin: "null" });
  assert.deepEqual(navigationDecision("//gideon.centra.ag/projects", HOSTED),
    { allowed: false, external: "", origin: "" });
});

test("hosted OAuth accepts a GitHub start only with an HTTPS return to the hosted origin", () => {
  const valid = authorize();
  assert.equal(hostedOAuthStart(valid.href, HOSTED), true);
  assert.equal(hostedOAuthStart(valid.href, "https://other.centra.ag"), false);

  const nonHttps = new URL(valid);
  nonHttps.protocol = "http:";
  assert.equal(hostedOAuthStart(nonHttps.href, HOSTED), false);
  const user = new URL(valid);
  user.username = "owner";
  assert.equal(hostedOAuthStart(user.href, HOSTED), false);
  const password = new URL(valid);
  password.password = "secret";
  assert.equal(hostedOAuthStart(password.href, HOSTED), false);
  const wrongPath = new URL(valid);
  wrongPath.pathname = "/auth/v1/authorize/";
  assert.equal(hostedOAuthStart(wrongPath.href, HOSTED), false);
  assert.equal(hostedOAuthStart(authorize("Google").href, HOSTED), false);
  assert.equal(hostedOAuthStart(authorize("github", "http://gideon.centra.ag/return").href, HOSTED), false);
  assert.equal(hostedOAuthStart(authorize("github", "https://gideon.centra.ag.attacker.test/return").href, HOSTED), false);
  const missingReturn = new URL(valid);
  missingReturn.searchParams.delete("redirect_to");
  assert.equal(hostedOAuthStart(missingReturn.href, HOSTED), false);
  assert.equal(hostedOAuthStart(authorize("github", "not a URL").href, HOSTED), false);
});

test("packaged hosted origin wins over environment and refreshes only the hosted registry row", () => {
  configureHostedEndpoint({ hostedUrl: HOSTED });
  try {
    const stale = { id: HOSTED_ENDPOINT_ID, label: "Old cloud", base_url: "https://old.example.org", kind: "remote" };
    const registry = { active: HOSTED_ENDPOINT_ID, endpoints: [OWNED, stale] };
    const env = { GIDEON_CLOUD_URL: "https://other.example.org/console" };
    assert.equal(hostedGatewayUrl(env), HOSTED);
    const refreshed = withHostedEndpoint(registry, env);
    assert.equal(refreshed.active, HOSTED_ENDPOINT_ID);
    assert.deepEqual(refreshed.endpoints[0], OWNED);
    assert.deepEqual(refreshed.endpoints[1], { id: HOSTED_ENDPOINT_ID, label: "Gideon Cloud",
      base_url: HOSTED, kind: "remote", device_session_ref: "" });
    assert.deepEqual(registry.endpoints, [OWNED, stale]);
    assert.deepEqual(withHostedEndpoint(refreshed, env), refreshed);
  } finally {
    configureHostedEndpoint(null);
  }
});

test("environment-only hosted selection preserves owned gateways and rejects unsafe URL metadata", () => {
  configureHostedEndpoint(null);
  const env = { GIDEON_CLOUD_URL: `  ${HOSTED}/console  ` };
  assert.equal(hostedGatewayUrl(env), HOSTED);
  const ownedActive = withHostedEndpoint({ active: OWNED.id, endpoints: [OWNED] }, env);
  assert.equal(ownedActive.active, OWNED.id);
  assert.deepEqual(ownedActive.endpoints.map((row) => row.id), [OWNED.id, HOSTED_ENDPOINT_ID]);
  const hostedActive = withHostedEndpoint({ active: "missing", endpoints: [] }, env);
  assert.equal(hostedActive.active, HOSTED_ENDPOINT_ID);
  assert.equal(hostedActive.endpoints.length, 1);

  for (const url of [
    "https://owner:secret@gideon.centra.ag",
    `${HOSTED}/?token=secret`,
    `${HOSTED}/#fragment`,
    "https://gideon.centra.ag:invalid-port",
  ]) {
    assert.equal(hostedGatewayUrl({ GIDEON_CLOUD_URL: url }), "");
  }
  assert.equal(hostedGatewayUrl({}), "");
  assert.equal(hostedGatewayUrl(null), "");
});

test("an absent hosted configuration removes a stale cloud row without changing an owned endpoint", () => {
  configureHostedEndpoint(null);
  const stale = { id: HOSTED_ENDPOINT_ID, base_url: "https://old.example.org" };
  const registry = { active: HOSTED_ENDPOINT_ID, endpoints: [stale, OWNED] };
  assert.deepEqual(withHostedEndpoint(registry, {}), { active: OWNED.id, endpoints: [OWNED] });
  assert.deepEqual(withHostedEndpoint({ ...registry, active: OWNED.id }, {}),
    { active: OWNED.id, endpoints: [OWNED] });
  assert.deepEqual(withHostedEndpoint(null, {}), { active: "", endpoints: [] });
});
