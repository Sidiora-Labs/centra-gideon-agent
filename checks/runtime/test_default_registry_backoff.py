from __future__ import annotations

import json
import logging
import shutil
import subprocess

from gideon.extensions.apps import catalog


def test_default_registry_warns_once_until_real_git_recovery(
    tmp_path, monkeypatch, caplog
):
    repo = tmp_path / "registry.git"
    source = repo.as_uri()
    monkeypatch.setenv("GIDEON_APP_REGISTRY_URL", source)
    monkeypatch.setattr(catalog, "_registry_cache", {})
    monkeypatch.setattr(catalog, "_registry_retry", {})
    caplog.set_level(logging.WARNING, logger=catalog.__name__)

    def fetch(now):
        return catalog._fetch_registry_index(source, is_git=True, now=now)

    def warnings():
        return [
            record
            for record in caplog.records
            if record.name == catalog.__name__ and record.levelno == logging.WARNING
        ]

    start = 1000.0
    assert fetch(start) is None
    assert len(warnings()) == 1
    assert source in warnings()[0].getMessage()
    assert "backoff" in warnings()[0].getMessage()
    retry_at, delay = catalog._registry_retry[catalog._git_source_key(source)]
    assert delay == catalog._REGISTRY_RETRY_INITIAL_SECS
    assert fetch(retry_at) is None
    assert len(warnings()) == 1
    next_retry, next_delay = catalog._registry_retry[catalog._git_source_key(source)]
    assert next_delay == delay * 2

    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "app-registry.json").write_text(
        json.dumps({"apps": [{"name": "recovered"}]})
    )
    subprocess.run(["git", "add", "app-registry.json"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=Registry test",
            "-c",
            "user.email=registry@example.invalid",
            "commit",
            "-qm",
            "Publish registry",
        ],
        cwd=repo,
        check=True,
    )

    assert fetch(next_retry - 1) is None
    assert len(warnings()) == 1
    recovered = fetch(next_retry)
    assert [pointer.name for pointer in recovered] == ["recovered"]
    assert catalog._registry_retry == {}
    assert len(warnings()) == 1

    shutil.rmtree(repo)
    assert fetch(next_retry + 1) == recovered
    assert fetch(next_retry + catalog._REGISTRY_TTL_SECS) is None
    assert len(warnings()) == 2
    _, new_delay = catalog._registry_retry[catalog._git_source_key(source)]
    assert new_delay == catalog._REGISTRY_RETRY_INITIAL_SECS


def test_nondefault_git_failure_does_not_start_default_warning_streak(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setenv("GIDEON_APP_REGISTRY_URL", (tmp_path / "default.git").as_uri())
    monkeypatch.setattr(catalog, "_registry_cache", {})
    monkeypatch.setattr(catalog, "_registry_retry", {})
    caplog.set_level(logging.WARNING, logger=catalog.__name__)
    assert (
        catalog._fetch_registry_index(
            (tmp_path / "custom.git").as_uri(), is_git=True, now=1000.0
        )
        is None
    )
    assert catalog._registry_retry == {}
    assert not [
        record
        for record in caplog.records
        if record.name == catalog.__name__ and record.levelno == logging.WARNING
    ]
