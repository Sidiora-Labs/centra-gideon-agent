import asyncio

from .wave_02 import durable_memory_context_journey


def test_wave_02_durable_memory_context(tmp_path):
    result = asyncio.run(durable_memory_context_journey(tmp_path))
    assert result["state"] == "complete"
    assert result["summary_invalidated"]
    assert result["second_writer_fenced"]
    assert result["restart_deduplicated"]

