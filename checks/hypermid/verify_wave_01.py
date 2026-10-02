"""Qualify local daemon integration without substituting a transport double."""

import asyncio
import json
from pathlib import Path
import tempfile

from waves.local_journey import authenticated_journey


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="hypermid-local-") as root:
        print(json.dumps(asyncio.run(authenticated_journey(Path(root)))))
