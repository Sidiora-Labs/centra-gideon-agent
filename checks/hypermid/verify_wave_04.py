"""Qualify the real fenced Wave 4 background integration journey."""

import json
import os
import tempfile
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
try:
    from .waves.wave_04 import run
except ImportError:
    from waves.wave_04 import run


def verify(root: Path) -> dict[str, object]:
    environment = find_dotenv(usecwd=True)
    if environment:
        load_dotenv(environment, override=False)
    os.environ.setdefault("HYPERMID_TEST_MODEL", "xai/grok-4.6(high)")
    return run(root)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="hypermid-wave-04-") as root:
        print(json.dumps(verify(Path(root)), sort_keys=True))
