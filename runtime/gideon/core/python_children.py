"""Refuse Python interpreter children when the desktop executable is frozen."""
import sys

INSTALL_COMMAND = "uv tool install --python 3.13 gideon-agent-harness"


class NeedsInterpreter(RuntimeError):
    """The requested child needs an interpreter this executable cannot provide."""


def available() -> bool:
    return not getattr(sys, "frozen", False)


def refusal(cannot: str) -> str:
    return f"The desktop app can't {cannot}; the version installed with `{INSTALL_COMMAND}` can."


def require(cannot: str) -> None:
    if not available():
        raise NeedsInterpreter(refusal(cannot))



def app_refusal(manifest) -> str:
    """Reject only app capabilities that need an unsupported interpreter child."""
    if available():
        return ""
    needs = []
    if manifest.sources:
        needs.append("run this app's Python parse scripts")
    if any(provider.execution == "sidecar" for provider in manifest.all_providers()):
        needs.append("create this app's Python sidecar environment")
    requirements = manifest.dependencies.pythonDependencies
    if requirements:
        from gideon.extensions.apps.app_python import unmet
        missing = unmet(requirements)
        if missing:
            needs.append("install this app's Python packages (" + ", ".join(missing) + ")")
    return refusal(" and ".join(needs)) if needs else ""
