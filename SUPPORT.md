# Support

Gideon is pre-1.0 software, and a small maintainer team runs it. There is no support
contract and no response time we can promise. Issues are handled on a best-effort basis,
so an answer may take a while. That is the honest state of things.

## Where to ask

Open an issue at https://github.com/sidiora-labs/centra-gideon-agent/issues

Security reports do not go there. Use the private channel described in
[SECURITY.md](SECURITY.md).

## Before you file

1. Read [docs/guides/getting-started.md](docs/guides/getting-started.md).
2. Check [docs/reference/CONFIG-REFERENCE.md](docs/reference/CONFIG-REFERENCE.md) for the
   setting you are fighting. Most surprising defaults are documented there.
3. Run `gideon doctor`. It checks dependencies, configuration, credentials, and
   connectivity, and prints what it found.
4. Confirm which `GIDEON_HOME` you are on. A second instance keeps its own state, so a fix
   in one home does nothing for the other, and a bug may not reproduce at all.

## What to include

- What you did.
- What you expected to happen.
- What happened instead, including the error text.
- The exact command you ran, or the click path you followed.
- How you installed Gideon.
- The revision, from `git rev-parse HEAD` in your checkout.

## What to leave out

- Gateway auth tokens, provider keys, and anything printed by `gateway --json-ready`.
- Private conversation content.
- Anything that belongs to a real customer.

If a reproduction needs credentials, use a synthetic one and an isolated `GIDEON_HOME`
instead of your own.

## Commands named here

The commands above, and the flags named on this page, are documented in
[docs/reference/cli.md](docs/reference/cli.md). That page mirrors the live help text, so
`gideon <command> --help` is the same information from the command line.
