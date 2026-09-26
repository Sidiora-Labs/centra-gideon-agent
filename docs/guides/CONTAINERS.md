# Running Gideon in containers

The container definitions build Gideon's gateway and console from this checkout. If you
have your own registry, set the image overrides explicitly rather than relying on these
local names.

This guide covers single-container and Docker Compose deployments: ports, volumes, the
`.env` pattern, backups, and updates.

## Quick start

From a checkout, build the combined image and start one container:

```bash
docker build -f infrastructure/docker/Dockerfile.backend --target single -t gideon:local .
docker run -d --name gideon --restart unless-stopped -p 127.0.0.1:10000:10000 -v gideon_home:/data gideon:local
docker logs gideon
```

Open the token URL in the logs. The console is served by the gateway at
`http://127.0.0.1:10000`. To supply provider keys, create `.env` and add
`--env-file .env` before `gideon:local` in the run command.

The existing two-service Compose path remains available from the same checkout:

```bash
cp .env.example .env         # fill in provider keys / options (all optional)
docker compose -f infrastructure/compose/compose.yaml -f infrastructure/compose/compose.build.yaml up -d --build
```

Two Compose services come up:

| Service | Image | Purpose |
|---|---|---|
| `gideon-gateway` | `gideon-gateway:local` | the agent gateway (dashboard API + channels) |
| `gideon-web` | `gideon-web:local` | nginx TLS/HTTP2 proxy serving the SPA + streaming to the gateway |

Set `GIDEON_GATEWAY_IMAGE` and `GIDEON_WEB_IMAGE` to complete image references when you
are using published images. Build the local defaults with `compose.build.yaml`:

```bash
docker compose -f infrastructure/compose/compose.yaml -f infrastructure/compose/compose.build.yaml up -d --build
```

## Ports

| Published port | Container | What |
|---|---|---|
| `127.0.0.1:10000` | single `:10000` | console and gateway over HTTP |
| `127.0.0.1:3000` | web `:80` | HTTP, 308-redirects to HTTPS |
| `127.0.0.1:3443` | web `:443` | **the app**: HTTPS + HTTP/2 (self-signed cert out of the box) |
| `127.0.0.1:10000` | Compose gateway `:10000` | gateway API/dashboard (normally reached via the web proxy) |

Every port binds to `127.0.0.1` by default, so the deployment stays private to the host
until you put it behind your own reverse proxy or change the bindings. For Compose,
open `https://127.0.0.1:3443` and accept the self-signed certificate. To replace
that certificate, mount a real one over `/etc/nginx/certs/gideon.{crt,key}`.

## Volumes

State lives in the named volume `gideon_home`, mounted at `/data` inside the gateway
container (`GIDEON_HOME=/data`, `GIDEON_WORKSPACE=/data/workspace`). It holds config,
credentials, memory, knowledge, apps, and the workspace, which is everything that has
to survive a container recreation. The single-container command uses the same volume.

```bash
docker exec gideon du -sh /data    # single-container state size
docker volume inspect gideon_home # volume details
```

State survives removal and recreation of the named container. It also survives
`docker compose down && docker compose up -d`; `docker compose down -v` removes the
Compose volume, so take a snapshot before using that option.

## Environment (`.env`)

The single-container command accepts `--env-file .env` before the image name. Compose
reads the repo-root `.env` through each service's `env_file`. Copy `.env.example` and
set only what you need. The common variables are:

| Variable | Default | Notes |
|---|---|---|
| `GIDEON_GATEWAY_IMAGE` | `gideon-gateway:local` | gateway image reference |
| `GIDEON_WEB_IMAGE` | `gideon-web:local` | console image reference |
| `GIDEON_PORT` | `10000` | gateway port inside the container |
| `GIDEON_BIND_HOST` | `0.0.0.0` (in both images) | so port-forwarding works |
| `GIDEON_AUTH_MODE` | `local_token` | only `none` is honored as an override, and it forces a loopback bind |
| `GIDEON_LOGIN_USER` | (unset) | seeds the owner login once, at first boot |
| `GIDEON_LOGIN_PASSWORD` | (unset) | the password for that login (≥12 characters) |

The images set `GIDEON_INSTALL_KIND=container` so the gateway knows it is a container
install. The single-container image also sets `GIDEON_DOCKER_MODE=single` for its
in-app update instructions.

## Getting the dashboard URL

In the default `local_token` auth mode the access URL (with a one-time token) is printed
to the gateway logs at startup, and it can be regenerated at any time:

```bash
docker exec gideon gideon token
```

For Compose, use `docker compose -f infrastructure/compose/compose.yaml exec gideon-gateway gideon token`.

## Owner login (a password instead of a token URL)

A container has no terminal to type a password at, so the credential can be seeded from
the environment on **first boot**:

```dotenv
# .env: the password must be at least 12 characters
GIDEON_LOGIN_USER=you
GIDEON_LOGIN_PASSWORD=a-long-passphrase-you-remember
```

Then turn the login form on once, inside the container, and restart:

```bash
docker exec gideon gideon auth enable
docker restart gideon
```

For Compose, run `docker compose -f infrastructure/compose/compose.yaml exec gideon-gateway gideon auth enable`, then `docker compose -f infrastructure/compose/compose.yaml restart gideon-gateway`. For the single container, supply `--env-file .env` on its first
`docker run` so the login values reach the gateway.

Three things worth knowing:

- **Seeding never overwrites.** If a credential already exists the variables are ignored,
  so leaving them in `.env` cannot reset a password you later changed. Rotate with
  `gideon auth set-password` (or clear the credential first).
- **Seeding does not enable the form.** Enrolling a credential and opening a front door
  are separate decisions, and `gideon auth enable` is the second one. Check either with
  `gideon auth status`.
- **The token URL keeps working.** Login is an *additional* way in, never a replacement,
  so a misconfigured password cannot lock you out of your own box.

Prefer a Docker/compose secret, or an `EnvironmentFile` with 0600 permissions, over a
world-readable `.env`. These two variables are as sensitive as the password itself.

> `GIDEON_AUTH_MODE=api_key` is **not** wired up: `AuthConfig.from_env` honors only
> `none` (which forces a loopback bind). Use the owner login above for headless access, or
> mint a long-lived token with `gideon token --ttl`.

## Backups

Snapshot state from **inside** the gateway container, so the archive captures the `/data`
volume exactly as the gateway sees it:

```bash
# create a snapshot (written under /data/snapshots)
docker exec gideon gideon snapshot

# list snapshots
docker exec gideon gideon snapshot --list

# copy one out to the host
docker cp 'gideon:/data/snapshots/<file>.tar.gz' .
```

Replace `<file>` with the name printed by `gideon snapshot --list`. For Compose,
replace `docker exec gideon` with `docker compose -f infrastructure/compose/compose.yaml exec gideon-gateway`, and use `docker compose -f infrastructure/compose/compose.yaml cp 'gideon-gateway:/data/snapshots/<file>.tar.gz' .` to copy the archive out.

For a full replacement restore, stop the gateway and run the restore in a temporary
container using the same volume:

```bash
docker stop gideon
docker run --rm --volumes-from gideon gideon:local gideon restore '/data/snapshots/<file>.tar.gz' --mode replace
docker start gideon
```

Take a snapshot before every upgrade.

## Updates

Container installs update by rebuilding or pulling an image and recreating the container.
There is no in-place self-update. For Compose with published image references:

```bash
# set GIDEON_GATEWAY_IMAGE and GIDEON_WEB_IMAGE to published image references first
docker compose -f infrastructure/compose/compose.yaml pull
docker compose -f infrastructure/compose/compose.yaml up -d
```

For the single-container checkout build, rebuild the image and recreate the named
container with the same `gideon_home` volume:

```bash
docker build -f infrastructure/docker/Dockerfile.backend --target single -t gideon:local .
docker stop gideon && docker rm gideon
docker run -d --name gideon --restart unless-stopped -p 127.0.0.1:10000:10000 -v gideon_home:/data gideon:local
```

Repeat any `--env-file` or other options you used on the original `docker run`.

State in `gideon_home` carries across the recreation. Snapshot before upgrading (see
[Backups](#backups)) and read the [CHANGELOG](../../CHANGELOG.md) for breaking changes.
Gideon is pre-1.0.

## Slack channel (optional)

The compose file includes an opt-in `gideon-slack` service behind the `with-slack`
profile. It runs `gideon slack` against the same volume:

```bash
docker compose -f infrastructure/compose/compose.yaml --profile with-slack up -d
```

## Troubleshooting

- **502 from the web proxy after recreating the gateway.** The nginx config re-resolves
  the gateway hostname per request (via `NGINX_ENTRYPOINT_LOCAL_RESOLVERS`), so this
  should self-heal within seconds. If it does not, run
  `docker compose restart gideon-web`.
- **Browser refuses the self-signed cert.** That is expected out of the box. Accept the
  exception, or mount a real cert over `/etc/nginx/certs/gideon.{crt,key}`.
- **`gideon token` says the gateway isn't running.** Check `docker ps` for the named
  `gideon` container, or `docker compose ps` for `gideon-gateway`. The healthcheck hits
  `/api/healthz`.
