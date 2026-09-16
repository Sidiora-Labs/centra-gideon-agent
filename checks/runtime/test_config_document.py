import json
import logging
from pathlib import Path

import pytest

from gideon.core.config.decoding import decode_configuration
from gideon.core.config.document import read_configuration, write_configuration
from gideon.core.config.loader import AppConfig, ConfigPreserveError


def test_separate_documents_do_not_share_default_lists():
    first = decode_configuration({}, AppConfig)
    second = decode_configuration({}, AppConfig)
    first.agent.subagent_cwd_allowed_roots.append("/first-only")
    assert "/first-only" not in second.agent.subagent_cwd_allowed_roots


def test_real_document_rewrite_preserves_provider_sections(tmp_path):
    path = tmp_path / "config.json"
    protected = {
        "providers": {"private": {"api_key": "test-value", "extra": [1, 2]}},
        "use_cases": {"chat": "private"},
        "slack": {"channels": ["team"]},
    }
    path.write_text(json.dumps(protected), encoding="utf-8")
    config = decode_configuration(
        {"agents": {"research": {"model": "chosen"}}}, AppConfig
    )
    write_configuration(path, config.to_dict(), ConfigPreserveError)
    saved = read_configuration(path, logging.getLogger(__name__))
    assert saved is not None
    assert {key: saved[key] for key in protected} == protected
    reloaded = decode_configuration(saved, AppConfig)
    assert reloaded.agents["research"].model == "chosen"
    assert reloaded.to_dict() == config.to_dict()


@pytest.mark.parametrize("content", ["{broken", "[]", "null"])
def test_invalid_existing_document_is_never_replaced(tmp_path, content):
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigPreserveError):
        write_configuration(path, AppConfig().to_dict(), ConfigPreserveError)
    assert path.read_text(encoding="utf-8") == content


def test_configuration_scanner_follows_the_real_policy_reader():
    from checks.harness.scanner import check_config_four_points

    root = Path(__file__).resolve().parents[2]
    files = list((root / "runtime/gideon/core/config").glob("*.py"))
    assert check_config_four_points(files, root) == []
