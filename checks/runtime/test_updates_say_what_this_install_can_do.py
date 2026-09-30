"""Update availability and unattended-apply status follow the install kind."""

from __future__ import annotations

import pytest

from gideon.operations import self_update


@pytest.mark.parametrize("kind", self_update.INSTALL_KINDS)
def test_only_a_source_checkout_applies_updates_unattended(kind: str) -> None:
    assert self_update.applies_updates_unattended(kind) is (kind == "git")


@pytest.mark.parametrize("kind", self_update.INSTALL_KINDS)
@pytest.mark.asyncio
async def test_update_check_reports_unattended_apply_for_install_kind(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(self_update, "detect_install_kind", lambda: kind)
    monkeypatch.setattr(self_update, "fetch_latest_release", _empty_release)
    monkeypatch.setattr(self_update, "commits_behind_upstream", _no_commits_behind)

    status = await self_update.build_update_status("0.2.0")

    assert status["kind"] == kind
    assert status["unattended_apply"] is (kind == "git")


async def _empty_release() -> dict[str, str]:
    return {}


async def _no_commits_behind(_project: str) -> int:
    return 0
