# Mobile data-safety evidence checklist

This is a draft release-review checklist for the Capacitor companion, not a
completed Google Play declaration or evidence of store acceptance. Store answers
must be reviewed against the exact distributed binary, its served web content,
configured services and current store policy.

## Source boundary

The [mobile package](../package.json) wraps web content served by a configured
Gideon gateway. The local bootstrap and gateway discovery are documented in
[the mobile README](../README.md). Installed Capacitor dependencies include the
push-notifications plugin; dependency presence alone does not establish active
registration or delivery.

The gateway can use configured providers, integrations, browser resources and
update checks. A companion shell does not make those destinations disappear.
Distinguish shell-originated requests from the remote page's requests, and inspect
which endpoints and credentials the release actually uses.

## Before submitting a declaration

- Inspect the exact native package, permissions, plugins and any analytics or
  crash-reporting additions.
- Observe bootstrap, pairing, normal use and notification traffic on the release
  build, including destination hosts and transmitted identifiers or content.
- Verify storage of gateway addresses, cookies, tokens and native preferences;
  check logout, unpairing and deletion behavior.
- Record the actual transport. User-selected HTTP does not provide the same
  protection as HTTPS or an encrypted private-network route.
- If push is enabled, verify registration, relay ownership, payload contents,
  logging and token retirement. Do not infer these from a plugin dependency.
- Evaluate the served gateway UI and configured integrations as well as the
  native bootstrap. Navigation restrictions alone do not prove absence of
  third-party requests.

Keep resulting evidence separate from proposed store answers. An assumed
self-hosted deployment, absent developer-operated backend or a passing source
test is insufficient by itself to select a universal “No data collected” answer.
Revisit the declaration whenever dependencies, native capabilities, hosting,
relays or served web content change.
