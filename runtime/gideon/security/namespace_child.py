"""Run the package's fixed namespace launcher through the frozen child interface."""
import os
from pathlib import Path
import sys


def main():
    arguments = sys.argv[1:]
    network = True
    if len(arguments) > 3 and arguments[3] == "--no-network":
        network = False
        arguments = [*arguments[:3], *arguments[4:]]
    if len(arguments) < 5 or arguments[0] not in {"owner", "standard", "cc", "strict"} or arguments[3] != "--":
        raise SystemExit("Invalid namespace child arguments")
    level, account_home, gideon_home = arguments[:3]
    if not Path(account_home).is_absolute() or not Path(gideon_home).is_absolute():
        raise SystemExit("Namespace owner paths must be absolute")
    os.environ["HOME"] = account_home
    os.environ["GIDEON_HOME"] = gideon_home
    from gideon.security.sandbox import _build_launcher_script

    # Only trusted package code is compiled; arguments contain policy/data, never Python.
    program = _build_launcher_script(level, network=network)
    sys.argv = ["gideon-namespace", *arguments[4:]]
    exec(compile(program, "gideon-namespace", "exec"), {"__name__": "__main__"})


if __name__ == "__main__":
    main()
