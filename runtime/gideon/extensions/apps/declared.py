"""Programs and packages core may start for the calling app."""

from gideon.extensions.apps.manifest import PROGRAM_YOU_NAME, AppManifest

UNNAMED = "so its install review never named it"


class NotDeclared(PermissionError):
    pass


def _calling_manifest() -> tuple[bool, AppManifest | None]:
    try:
        from gideon.extensions.apps.code_provenance import owner
    except ImportError:
        raise NotDeclared(
            "The calling app cannot be identified; no program or package was started"
        ) from None
    app = owner()
    if app is None:
        return False, None
    from gideon.extensions.providers.registry import get_provider_registry

    record = get_provider_registry().get(app)
    return True, record.manifest if record is not None else None


def declares_npm_package(package: str) -> bool:
    is_app, manifest = _calling_manifest()
    return not is_app or (
        manifest is not None and package in manifest.dependencies.npmPackages
    )


def declared_programs() -> set[str] | None:
    is_app, manifest = _calling_manifest()
    if not is_app:
        return None
    if manifest is None:
        return set()
    return {
        entry.program
        for entry in manifest.launches
        if entry.program != PROGRAM_YOU_NAME
    }


def declared_hosts(app: str, program: str) -> tuple[str, ...]:
    from gideon.extensions.providers.registry import get_provider_registry

    record = get_provider_registry().get(app)
    manifest = record.manifest if record is not None else None
    if manifest is None:
        return ()
    entries = [entry for entry in manifest.launches if entry.program == program]
    hosts = [host for entry in entries for host in entry.hosts]
    if program in {"npm", "npx", "pnpm", "pnpx", "yarn", "bun", "bunx"} and (
        manifest.dependencies.npmPackages or any(entry.npmPackage for entry in entries)
    ):
        hosts.append(
            "registry.yarnpkg.com" if program == "yarn" else "registry.npmjs.org"
        )
    return tuple(dict.fromkeys(hosts))
