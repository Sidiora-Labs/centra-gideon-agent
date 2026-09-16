# Gideon security

Gideon can access local files, run tools, and communicate with configured services. Gateway credentials and the machine account running the process therefore grant meaningful access. The runtime contains authentication, approval, credential-handling, application-permission, command-screening, and audit mechanisms; their presence is not a security certification or a guarantee that every integration has the same enforcement.

The current architecture and limitations are described in [docs/architecture/security.md](docs/architecture/security.md), [docs/security/threat-model.md](docs/security/threat-model.md), and [docs/security/limitations.md](docs/security/limitations.md). Read those documents alongside the implementation for the revision being deployed.

## Report a suspected vulnerability

Use an established private channel to the operator or maintainer who supplied this checkout. This repository does not designate a public security inbox, hosted advisory endpoint, response deadline, or supported release matrix. If the deployed repository offers private vulnerability reporting, verify that it belongs to the operator before sending a report.

Include the affected revision and component, the deployment context, reproduction steps, and the trust boundary crossed. Use a minimal demonstration with synthetic credentials and isolated state. Remove real tokens, private conversation content, and personal data from attachments. Coordinate disclosure through the same private channel.

Relevant reports include unauthorized gateway access, permission or sandbox bypass, secret exposure, unsafe application installation, and untrusted input causing actions outside the user's granted authority. Identify whether an issue occurs with the normal approval configuration or requires an explicitly permissive setting.

## Operate and develop with isolated state

Set `GIDEON_HOME` explicitly for development, tests, and separate instances. Configuration and private state otherwise live under `~/.gideon`. Keep this directory and its backups accessible only to the intended account, and do not commit it to the repository.

Treat gateway authentication tokens, provider credentials, and authenticated startup output as secrets. In particular, `gateway --json-ready` prints connection information that includes a token. Redact it before sharing logs.

Use the approval mode appropriate to the actions being granted. `gateway --approval yolo` explicitly permits automatic tool approval; it changes the authority given to the agent. Review application permissions and provider configuration before enabling an integration, and check the relevant platform's isolation behavior before relying on it.

For a suspected compromise, stop the affected gateway, revoke or rotate exposed credentials with their issuing services, and preserve necessary evidence in a restricted location. Review state and installed applications before restarting. Security checks should run against an isolated copy with test credentials.

## Qualification boundary

The ongoing rewrite has focused local checks. It has not established a complete security audit, universal sandbox support, successful operation of every provider, or a certified native release. Deployment decisions require evidence for the particular configuration, platform, integrations, and revision being used.
