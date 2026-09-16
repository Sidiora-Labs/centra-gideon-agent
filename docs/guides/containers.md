# Running Gideon in containers

The container definitions build Gideon's gateway and console from this checkout.
Use explicit image overrides for a registry that you have configured and published.

This guide covers a self-hosted Docker Compose deployment: ports, volumes, the
`.env` pattern, backups, and updates.

## Quick start

From a checkout (or after downloading `infrastructure/compose/compose.yaml` and
`.env.example`):

```bash
cp .env.example .env         # fill in provider keys / options (all optional)
docker compose -f infrastructure/compose/compose.yaml -f infrastructure/compose/compose.build.yaml up -d --build
```

Two services come up:

| Service | Image | Purpose |
|---|---|---|
| `gideon-gateway` | `gideon-gateway:local` | the agent gateway (dashboard API + channels) |
| `gideon-web` | `gideon-web:local` | nginx TLS/HTTP2 proxy serving the SPA + streaming to the gateway |

Set `GIDEON_GATEWAY_IMAGE` and `GIDEON_WEB_IMAGE` to complete image references
when using published images. Build the local defaults with `compose.build.yaml`:

```bash
docker compose -f infrastructure/compose/compose.yaml -f infrastructure/compose/compose.build.yaml up -d --build
```

## Ports

| Published port | Container | What |
|---|---|---|
| `127.0.0.1:3000` | web `:80` | HTTP — 308-redirects to HTTPS |
| `127.0.0.1:3443` | web `:443` | **the app** — HTTPS + HTTP/2 (self-signed cert out of the box) |
| `127.0.0.1:10000` | gateway `:10000` | gateway API/dashboard (bound to loopback; normally reached via the web proxy) |

All ports bind to `127.0.0.1` by default — the deployment is private to the host
until you put it behind your own reverse proxy or change the bindings. Open
`https://127.0.0.1:3443` and accept the self-signed certificate (mount a real
cert over `/etc/nginx/certs/gideon.{crt,key}` to replace it).

## Volumes

State lives in the named volume `gideon_home`, mounted at `/data` inside
the gateway container (`GIDEON_HOME=/data`). It holds config, credentials,
memory, knowledge, apps, and the workspace — everything that must survive a
container recreation.

```bash
docker compose -f infrastructure/compose/compose.yaml exec gideon-gateway du -sh /data   # inspect state size
docker volume ls | grep gideon_home                                              # find the volume
```

State survives `docker compose down && docker compose up -d` because the volume
outlives the containers. It is **removed** by `docker compose down -v` — don't
run that unless you mean to wipe state (snapshot first).

## Environment (`.env`)

Compose reads the repo-root `.env` (via each service's `env_file`). Copy
`.env.example` and set only what you need — every variable is optional with a
sensible default. Common ones:

| Variable | Default | Notes |
|---|---|---|
| `GIDEON_GATEWAY_IMAGE` | `gideon-gateway:local` | gateway image reference |
| `GIDEON_WEB_IMAGE` | `gideon-web:local` | console image reference |
| `GIDEON_PORT` | `10000` | gateway port inside the container |
| `GIDEON_BIND_HOST` | `0.0.0.0` (in compose) | so port-forwarding works |
| `GIDEON_AUTH_MODE` | `local_token` | only `none` is honored as an override, and it forces a loopback bind |
| `GIDEON_LOGIN_USER` | — | seeds the owner login once, at first boot |
| `GIDEON_LOGIN_PASSWORD` | — | the password for that login (≥12 characters) |

The images set `GIDEON_INSTALL_KIND=container` so the gateway knows it is a
container install — the in-app Updates panel then shows the correct update
instructions (pull + up) instead of a git/pip update flow.

## Getting the dashboard URL

In the default `local_token` auth mode the access URL (with a one-time token) is
printed to the gateway logs at startup and can be regenerated:

```bash
docker compose -f infrastructure/compose/compose.yaml exec gideon-gateway gideon token
```

## Owner login (a password instead of a token URL)

A container has no terminal to type a password at, so the credential can be seeded from the
environment on **first boot**:

```dotenv
# .env — the password must be at least 12 characters
GIDEON_LOGIN_USER=you
GIDEON_LOGIN_PASSWORD=a-long-passphrase-you-remember
```

Then turn the login form on (once, inside the container) and restart:

```bash
docker compose -f infrastructure/compose/compose.yaml exec gideon-gateway gideon auth enable
docker compose -f infrastructure/compose/compose.yaml restart gideon-gateway
```

Three things worth knowing:

- **Seeding never overwrites.** If a credential already exists the variables are ignored, so
  leaving them in `.env` cannot reset a password you later changed. Rotate with
  `gideon auth set-password` (or clear the credential first).
- **Seeding does not enable the form.** Enrolling a credential and opening a front door are
  separate decisions — `gideon auth enable` is the second one. Check either with
  `gideon auth status`.
- **The token URL keeps working.** Login is an *additional* way in, never a replacement, so a
  misconfigured password can't lock you out of your own box.

Prefer a Docker/compose secret or an `EnvironmentFile` with 0600 permissions over a
world-readable `.env` — these two variables are as sensitive as the password itself.

> `GIDEON_AUTH_MODE=api_key` is **not** wired up: `AuthConfig.from_env` honors only
> `none` (which forces a loopback bind). Use the owner login above for headless access, or
> mint a long-lived token with `gideon token --ttl`.

## Backups

Snapshot state from **inside** the gateway container so the archive captures the
`/data` volume exactly as the gateway sees it:

```bash
# create a snapshot (written under /data/snapshots)
docker compose -f infrastructure/compose/compose.yaml exec gideon-gateway gideon snapshot

# list snapshots
docker compose -f infrastructure/compose/compose.yaml exec gideon-gateway gideon snapshot --list

# copy one out to the host (resolve the container id from `docker compose ps -q`)
docker compose -f infrastructure/compose/compose.yaml cp gideon-gateway:/data/snapshots/<file>.tar.gz .
```

Restore by copying an archive back in and running
`gideon restore <path>` inside the container. Take a snapshot before every
upgrade.

## Updates

Container installs update by pulling the new image and recreating — there is no
in-place self-update (the app's Updates panel shows exactly these commands for a
container install):

```bash
# pin the new release first if you don't track `latest`
#   GIDEON_IMAGE_TAG=vX.Y.Z   (in .env)
docker compose -f infrastructure/compose/compose.yaml pull
docker compose -f infrastructure/compose/compose.yaml up -d
```

State in `gideon_home` carries across the recreation. Snapshot before
upgrading (see [Backups](#backups)); read the
[CHANGELOG](../../CHANGELOG.md) for breaking changes (Gideon is pre-1.0).

## Slack channel (optional)

The compose file includes an opt-in `gideon-slack` service behind the
`with-slack` profile (it runs `gideon slack` against the same volume):

```bash
docker compose -f infrastructure/compose/compose.yaml --profile with-slack up -d
```

## Troubleshooting

- **502 from the web proxy after recreating the gateway** — the nginx config
  re-resolves the gateway hostname per request (via `NGINX_ENTRYPOINT_LOCAL_RESOLVERS`),
  so this should self-heal within seconds; if not, `docker compose restart gideon-web`.
- **Browser refuses the self-signed cert** — expected out of the box; accept the
  exception, or mount a real cert over `/etc/nginx/certs/gideon.{crt,key}`.
- **`gideon token` says the gateway isn't running** — check
  `docker compose ps` shows `gideon-gateway` healthy; the healthcheck hits
  `/api/healthz`.
