from __future__ import annotations

import asyncio

from checks.hypermid.verify_wave_07 import run_wave_07_journey


def test_wave_07_real_lifecycle_and_native_administration() -> None:
    asyncio.run(run_wave_07_journey())
