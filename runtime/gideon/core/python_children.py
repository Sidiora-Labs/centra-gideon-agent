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
