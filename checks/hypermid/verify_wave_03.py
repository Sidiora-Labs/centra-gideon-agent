"""Qualify the live daemon knowledge-to-primary-prompt journey."""

import asyncio
import json
import tempfile
from pathlib import Path

from waves.wave_03 import primary_knowledge_journey


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="hypermid-wave-03-") as root:
        result = asyncio.run(primary_knowledge_journey(Path(root)))
        print(json.dumps(result, sort_keys=True))
