# Security

Gideon runs on your machine, with your files and your credentials. It reads local files,
runs tools, and talks to services you configure. The gateway token and the account the
gateway runs under are therefore real access to that machine. Treat both the way you
treat a login shell.

## What is in place, and what that is worth

The runtime ships authentication, approval prompts, credential handling, application
permissions, command screening, and an audit log. We built these to be enforced where the
action happens, not just described to the model.

Their presence is not a certification. Not every integration goes through every control
in the same way, and a green test run is not a statement about your deployment. For the
places we know enforcement is weaker, read
[limitations](docs/security/LIMITATIONS.md) for the revision you are running.

## What the project collects

Nothing. Gideon sends no usage telemetry. Nothing about your usage leaves your machine
unless you configure an integration that sends it.

Every hostname that shipped code can reach is listed in
[docs/architecture/NETWORK_EGRESS_HOSTS.txt](docs/architecture/NETWORK_EGRESS_HOSTS.txt),
each with a judgment about whether it is fetched and by whose action. If code ever reaches
a host that is not declared there, a test fails.

## Report a suspected vulnerability

Send a report through a private channel to the operator or maintainer who gave you this
checkout. We designate no public security inbox, no advisory endpoint, and no response
deadline, and we do not promise a response time.

Include:

- the revision and the component
- how it is deployed
- the steps to reproduce it
- which trust boundary was crossed

Use synthetic credentials and isolated state. Strip real tokens, private conversations,
and personal data before you send anything.

Worth reporting: unauthorized gateway access, permission or sandbox bypass, secret
exposure, unsafe application installation, and untrusted input that causes an action
outside the authority you granted.

## Keep state isolated

Set `GIDEON_HOME` for development, tests, and second instances. The default is `~/.gideon`.
Keep that directory readable only by the account that should have it, and never commit it.

A second instance keeps its own state, so check which home you are pointed at before you
report a problem.

## Handle secrets

These are secrets: gateway auth tokens, provider credentials, and the output of
`gateway --json-ready`, which prints a token. Redact them before you share a log or paste
output into a report.

`gateway --approval yolo` lets tools run without approval. It changes the authority you are
granting, so use it on purpose and turn it off when you are done.

Review application permissions and provider configuration before you enable an
integration, and check what isolation your platform actually gives you before you rely on
it.

## If you think you are compromised

1. Stop the gateway.
2. Rotate the exposed credentials with whoever issued them.
3. Keep the evidence in a place only you can read.
4. Review state and installed applications before you restart.

## Read more

- [Architecture and controls](docs/architecture/SECURITY.md)
- [Threat model](docs/security/THREAT_MODEL.md)
- [Limitations](docs/security/LIMITATIONS.md)
- [Signing](docs/security/SIGNING.md)
- [Review scope](docs/security/REVIEW_SCOPE.md)
- [Network egress hosts](docs/architecture/NETWORK_EGRESS_HOSTS.txt)

Gideon is licensed under Apache 2.0. See [LICENSE](LICENSE). The source is at
https://github.com/Sidiora-Labs/centra-gideon-agent.
