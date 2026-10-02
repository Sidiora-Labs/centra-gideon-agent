from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from waves.wave_02 import durable_memory_context_journey


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="hypermid-wave-02-") as root:
        result = asyncio.run(durable_memory_context_journey(Path(root)))
        print(json.dumps(result, sort_keys=True))
