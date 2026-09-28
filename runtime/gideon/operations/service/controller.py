"""Platform dispatch for service install/uninstall/status.

CLI entry points should call functions in this module rather than
importing :mod:`gideon.operations.service.linux` or :mod:`gideon.operations.service.macos`
directly. This keeps the dispatch logic in one place and makes the
``UNSUPPORTED`` path produce consistent error output.
"""

import sys

from gideon.operations.service import linux, macos
from gideon.operations.service.common import Platform, current_platform
from gideon.operations.service.environment import (
    ServiceEnvironmentError,
    resolve_service_environment,
)


def _unsupported_message() -> None:
    print(
        "❌ gideon service management is only supported on Linux (systemd)\n"
        "   and macOS (launchd). On other platforms run `gideon gateway`\n"
        "   directly or wrap it in tmux/screen yourself.",
        file=sys.stderr,
    )


def install_service(
    env_additions: tuple[str, ...] = (), env_removals: tuple[str, ...] = ()
) -> int:
    """Install and start the platform service.

    Returns 0 on success, non-zero otherwise. On Linux the install
    prompts for sudo on first use to write
    ``/etc/systemd/system/gideon.operations.service`` and to run
    ``systemctl daemon-reload / enable / restart``. The gateway itself
    runs as ``User=$USER`` once started — gideon code is never
    invoked under sudo. On macOS no sudo is required. The CLI is
    expected to surface the sudo prompt to a real terminal.
    """
    try:
        resolve_service_environment(env_additions, env_removals)
    except ServiceEnvironmentError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    plat = current_platform()
    if plat == Platform.SYSTEMD:
        try:
            if env_additions or env_removals:
                linux.install(env_additions, env_removals)
            else:
                linux.install()
        except linux.ServiceInstallError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        print("✅ gideon service installed and started.")
        print(f"   unit: {linux.UNIT_PATH}")
        print()
        print("   Status: gideon service status")
        print("   Logs:   gideon logs -f")
        print("   Remove: gideon service uninstall")
        return 0
    if plat == Platform.LAUNCHD:
        try:
            if env_additions or env_removals:
                macos.install(env_additions, env_removals)
            else:
                macos.install()
        except macos.ServiceInstallError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        print("✅ gideon service installed and started.")
        print(f"   plist: {macos.PLIST_PATH}")
        print()
        print("   Status: gideon service status")
        print(f"   Logs:   tail -f {macos.STDOUT_LOG}")
        print("   Remove: gideon service uninstall")
        return 0
    _unsupported_message()
    return 2


def uninstall_service() -> int:
    """Stop and remove the platform service. Idempotent."""
    plat = current_platform()
    if plat == Platform.SYSTEMD:
        linux.uninstall()
        print("✅ gideon service stopped and removed.")
        return 0
    if plat == Platform.LAUNCHD:
        macos.uninstall()
        print("✅ gideon service stopped and removed.")
        return 0
    _unsupported_message()
    return 2


def service_status() -> int:
    """Print the platform service status. Returns 0 if active, 1 if inactive, 2 if unsupported."""
    plat = current_platform()
    if plat == Platform.SYSTEMD:
        print(linux.status())
        configured = linux.environment_status()
        if configured:
            print(configured)
        return 0 if linux.is_active() else 1
    if plat == Platform.LAUNCHD:
        print(macos.status())
        configured = macos.environment_status()
        if configured:
            print(configured)
        return 0 if macos.is_active() else 1
    _unsupported_message()
    return 2


def is_service_active() -> bool:
    """Return True if a gideon service is installed and currently running."""
    plat = current_platform()
    if plat == Platform.SYSTEMD:
        return linux.is_active()
    if plat == Platform.LAUNCHD:
        return macos.is_active()
    return False


def stop_service() -> bool:
    """Stop the platform service if active. Returns True if a service was stopped."""
    plat = current_platform()
    if plat == Platform.SYSTEMD:
        if linux.is_active():
            linux.stop()
            return True
        return False
    if plat == Platform.LAUNCHD:
        if macos.is_active():
            macos.stop()
            return True
        return False
    return False


def restart_service() -> bool:
    """Restart the platform service if installed. Returns True if a service was
    restarted (so the caller knows not to spawn a foreground gateway itself)."""
    plat = current_platform()
    if plat == Platform.SYSTEMD:
        if linux.is_active():
            linux.restart()
            return True
        return False
    if plat == Platform.LAUNCHD:
        if macos.is_active():
            macos.restart()
            return True
        return False
    return False
