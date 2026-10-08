# Platforms

Gideon includes a Python gateway, a Rust Hypermid daemon, a web console, Electron desktop
packaging and a Capacitor mobile shell. A source path or build target is not a qualified
release for every host. Use the actual distribution and configuration for your platform.

| Environment | Implemented path | What to check |
|---|---|---|
| Linux | Python gateway, systemd, Linux containers and desktop packaging | Architecture, native dependencies, configured programs and OS wrapping |
| macOS | Python gateway, launchd and desktop packaging | Architecture, permissions, native dependencies, signing/notarization of supplied distribution |
| Windows with WSL2 | Linux runtime inside a WSL distribution | Host filesystem, forwarding and service availability |
| Windows with Docker Desktop | Linux container recipe | Available image architecture, persistent volume, networking and host capabilities |
| Native Windows | Platform-specific source support exists in parts of the tree | No native desktop packaging/release qualification is established by this guide |
| iOS/Android | Capacitor shell connected to a gateway | Native permissions, configured gateway access, signing and store distribution |

## Source requirements and packaging

The [checkout recipe](../../README.md#run-from-a-checkout) requires Python 3.12+,
Node.js 22.12+ and npm for console builds. Runtime wheel builds compile Hypermid with
Rust/Cargo; the checkout pins Rust 1.91.1. An explicitly supplied compatible daemon can
be used through `GIDEON_PREBUILT_HYPERMID_DAEMON` during packaging.

The console and assistant surface have separate build paths. A complete supplied wheel
or container can include native binaries and web assets, but the installer name alone
does not prove that those assets exist. No public package index, image registry or
release endpoint is required or assumed. See [Getting started](GETTING_STARTED.md) and
[Containers](CONTAINERS.md).

## Optional local models

`pyproject.toml` declares `embeddings`, `stt`, `tts` and aggregate `models` extras.
The locked dependency set and the target Python/OS/architecture determine which wheels
can install. A wheel entry in `uv.lock` is resolver evidence, not proof that every
extra installs or loads on every architecture. Do not infer native library, GPU or
Alpine compatibility from one package's wheel availability.

Model memory and disk needs depend on the selected runtime and model. A remote binding
can avoid a local inference stack, but it does not make every optional capability
available. Diagnose an actual failed load instead of assuming any out-of-memory event
is caused by one library. `gideon doctor` distinguishes setup evidence from a completed
inference or transcription.

On macOS, speech and embedding libraries can bring incompatible native/OpenMP runtimes
into one process. Package discovery without import is not a successful model load.
Exercise the selected integration on the target host before relying on it.

## Windows through WSL2

From a Linux shell in WSL, follow the source recipe or install an explicitly supplied
compatible Linux wheel. For the checkout bootstrap:

```bash
sh infrastructure/website/install.sh
gideon setup
gideon gateway
```

The bootstrap requires its documented toolchain when building from source. Use the
access URL the gateway prints; whether Windows can reach it depends on that WSL host's
localhost forwarding and networking configuration.

Keep database/state files on the Linux filesystem when possible. Mounted Windows paths
have different filesystem and locking behavior; validate them before placing
`GIDEON_HOME` or active workspaces there. This guide does not supply a measured universal
performance ratio.

`gideon service install` uses the Linux systemd path. It requires systemd to be available
inside the chosen WSL distribution. Without it, run the gateway in a foreground shell
or configure an appropriate host launcher. Do not assume the VM starts solely because
a Linux service file was installed.

## Windows through Docker Desktop

Run the Linux container recipe from the checkout, using a Docker engine able to build
or run the selected image. The Windows host does not need the runtime's Python installed.
Use the repo-root `.env` and the persistent named volume as described in
[Containers](CONTAINERS.md). Paths passed into the runtime are container paths.

```powershell
copy .env.example .env
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.build.yaml up -d --build gideon-gateway gideon-web
```

Default published ports bind host loopback. A named volume keeps runtime state inside
the container engine's managed filesystem. Avoid assuming an arbitrary NTFS bind mount
has the same locking and permission behavior. `docker compose down` keeps the volume;
`down -v` deletes it. Take a snapshot before removing state.

Container restart policy replaces the gateway's system-service installation. It depends
on the container engine itself running. Host desktop/camera/audio/browser capabilities
are not automatically available inside a container.

See [Desktop](DESKTOP.md), [Companion apps](COMPANION_APPS.md) and
[Security limits](../security/LIMITATIONS.md).
