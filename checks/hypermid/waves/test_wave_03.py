"""Wave 3 context integration acceptance."""

import asyncio

from .wave_03 import integrated_context_journey, primary_knowledge_journey


def test_gideon_context_integration(tmp_path):
    result = integrated_context_journey(tmp_path)
    assert result["keyword_fallback"]
    assert result["redacted_inspection"]


def test_primary_daemon_knowledge_reaches_prompt(tmp_path):
    result = asyncio.run(primary_knowledge_journey(tmp_path))
    assert result["daemon_knowledge_in_prompt"]
    assert result["inspection_redacted"]
