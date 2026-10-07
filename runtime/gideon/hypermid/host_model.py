"""Background-summary bridge over Gideon's already configured model authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gideon.integrations.llm.base import ModelProvider

    from .history import HistoryJournal

from .summarizer import (
    SummaryCandidate,
    SummaryJob,
    model_provider_authority,
    summarize_journal_job,
)


@dataclass(frozen=True, slots=True)
class HostSummaryModel:
    provider: ModelProvider

    async def summarize(
        self,
        job: SummaryJob,
        journal: HistoryJournal,
        *,
        source_token_count: int,
        now_ms: int,
        timeout_seconds: float = 90.0,
    ) -> SummaryCandidate:
        """Run a leased summary job without exporting provider credentials."""

        return await summarize_journal_job(
            job,
            journal,
            source_token_count=source_token_count,
            now_ms=now_ms,
            timeout_seconds=timeout_seconds,
            authority=model_provider_authority(self.provider),
        )


__all__ = ["HostSummaryModel"]
