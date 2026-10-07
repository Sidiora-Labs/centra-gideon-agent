# Desktop shell

Gideon's Electron shell loads the gateway's dashboard and adds supported native
capabilities. Local-runtime mode and packaged Windows hosted mode have different
lifecycles; do not assume every desktop package starts a local Python gateway.

## Local runtime mode

On the local-runtime path, the shell starts its prepared backend, waits for its actual
ready address, and loads that dashboard. Quitting requests shutdown and waits for the
owned processes, with escalation and diagnostic reporting when they do not exit cleanly.
The shell also manages the local Hypermid process where required by its backend.

Native features include tray presence, configurable global dictation, notifications,
login-item integration where supported, and capability reporting. Availability depends
on the platform, OS permissions, and connected local bridge. Settings → Security shows
the actual reported capability state rather than making a browser tab a native shell.

Closing a window can hide it when a usable tray exists. The tray provides a route back
into the dashboard; unavailable tray support must not leave an inaccessible hidden app.
Its listening indicator takes precedence over an approval badge. Quick Capture opens
the associated capture destination; check the current screen's offered controls rather
than assuming the tray action itself wrote a note.

### Dictation

The global shortcut toggles microphone capture: press once to start and again to stop.
It is not a held-key recorder. The controller imposes a two-minute maximum capture and
reports shortcut validation or registration failure. Dictation uses the configured
speech-to-text binding and a supported active composer.

Microphone permission is controlled by the operating system. System audio is reported
unavailable by the current capability implementation; microphone access does not grant
system-output recording. Verify permission prompts and visual capture indicators on the
actual target platform before claiming native behavior is qualified.

### Notifications and login

Per-kind notification rules can request native delivery when the connected shell exposes
that capability. Badge and digest behavior do not become native interruptions merely
because the target is selected. OS settings can suppress a notification after Gideon
requested it; a delivery request is not proof that a banner appeared.

Login-item support reads and writes the platform's registration where implemented.
Use the reported state and refusal reason. Platform-specific controls are not universal
promises for every desktop environment.

## Paired gateways

The gateway switcher can navigate to a paired gateway rather than the locally owned one.
Pair using the target gateway's actual device flow, check the destination, and keep its
credential in the browser session rather than copying a token into shell registry data.

Address validation distinguishes loopback, private-network, and public destinations.
Public plaintext HTTP is refused; admitted network destinations require the applicable
confirmation. A suspicious or conflicting resolved address is not silently treated as
local. The desktop capability bridge belongs to the locally owned gateway and is not
attached to arbitrary paired origins.

Endpoint state and health are separate. A timeout is not the same as an authentication
refusal. Revoking one device session should not clear unrelated gateways. See
[companion apps](COMPANION_APPS.md) and [remote access](REMOTE_ACCESS.md).

## Packaged Windows mode

`apps/desktop/package.json` declares `dist:win`, implemented by
`apps/desktop/tooling/build-windows.mjs`. It builds an x64 NSIS installer with an explicit
hosted configuration and a SHA-256 manifest. The build accepts `GIDEON_CLOUD_URL` as an
HTTPS origin; the script has a default origin, which is a configuration value rather than
proof of that service's current availability.

A packaged Windows shell loads that hosted origin, does not start the local gateway,
and does not attach the local desktop capability bridge. Invalid or missing packaged
configuration is an error, not a reason to fall back to an unrelated local backend.
This differs from running the Linux runtime under WSL2 or Docker Desktop.

The presence of this build path does not establish a built, signed, published, or
exercised Windows installer. Signing depends on supplied publisher credentials.

## Build and release boundaries

From the repository root, the local-runtime packaging commands are:

```bash
make desktop             # prepare the backend and desktop staging
make desktop-dist        # macOS packaging
make desktop-dist-linux  # Linux AppImage / Debian packaging
```

The workspace also declares `start`, `dist`, `dist:linux`, and `dist:win` scripts. Native
packaging requires the actual platform toolchain and prepared resources; Node dependency
installation alone is insufficient. macOS signing/notarization and store distribution
require separate credentials and release work.

Use the operator's verified release channel for installers and integrity information.
Source presence and package configuration do not establish target-platform UI, permissions,
safe shutdown, or visual qualification. Keep the shell and its backend compatible when
updating and preserve the active Gideon home.

See [platforms](PLATFORMS.md) and
[configuration](../reference/CONFIGURATION.md).
