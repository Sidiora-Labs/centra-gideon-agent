"""Tests for the _validate_agent fallback chain in subagent.py.

Heavy dependencies are stubbed at the sys.modules level so subagent.py imports
without the full runtime.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_STUBS = [
    "gideon.cognition.context",
    "gideon.engine.hooks",
    "gideon.extensions.providers",
    "gideon.integrations.llm.base",
    "gideon.security.sel",
    "gideon.engine.session",
    "gideon.core.textfmt",
    "gideon.operations.stats",
]


@pytest.fixture(autouse=True)
def _stub_modules():
    """Inject stub modules so subagent.py can be imported."""
    originals = {}
    for mod_name in _STUBS:
        originals[mod_name] = sys.modules.get(mod_name)
        stub = types.ModuleType(mod_name)
        if mod_name == "gideon.integrations.llm.base":
            stub.EVENT_COMPLETE = "complete"
            stub.EVENT_PERMISSION_REQUEST = "permission"
            stub.EVENT_TEXT_CHUNK = "text"
            stub.EVENT_TOOL_CALL = "tool_call"
            stub.LLMEvent = type("LLMEvent", (), {})
            stub.ModelProvider = type("ModelProvider", (), {})
        if mod_name == "gideon.engine.hooks":
            stub.TOOL_AUTO_APPROVE = "auto"
            stub.TOOL_DENY = "deny"
            stub.fire_tool_hooks = MagicMock()
            stub.safe_read_file = lambda path: ""
            stub.get_global_hook_store = MagicMock()
        if mod_name == "gideon.core.textfmt":
            stub.extract_options = lambda x: (x, [])
        if mod_name == "gideon.operations.stats":
            stub.Stats = MagicMock
        if mod_name == "gideon.security.sel":
            stub.sel = MagicMock()
        if mod_name == "gideon.cognition.context":
            stub.PromptAssembler = MagicMock
        if mod_name == "gideon.engine.session":
            stub.ConversationDirectory = MagicMock
        sys.modules[mod_name] = stub

    sys.modules.pop("gideon.engine.subagent", None)

    yield

    for mod_name in _STUBS:
        if originals[mod_name] is None:
            sys.modules.pop(mod_name, None)
        else:
            sys.modules[mod_name] = originals[mod_name]
    sys.modules.pop("gideon.engine.subagent", None)


def _config_with_agents(*names: str) -> MagicMock:
    """Return a stub AppConfig whose ``.agents`` is keyed by *names*."""
    cfg = MagicMock()
    cfg.agents = {n: MagicMock() for n in names}
    return cfg


def test_found_returns_requested():
    from gideon.engine.subagent import _validate_agent

    with patch(
        "gideon.core.config.loader.AppConfig.load",
        return_value=_config_with_agents("code-reviewer", "gideon"),
    ):
        name, err = _validate_agent("code-reviewer")
        assert name == "code-reviewer"
        assert err == ""


def test_unknown_agent_returns_typed_error_naming_valid_agents():
    """C1.3: an unconfigured agent name is a TYPED error naming the valid agents —
    NOT a silent downgrade to the default. A fan-out that named the wrong agent used
    to run entirely on gideon with only a log line; now it fails loudly."""
    from gideon.engine.subagent import _validate_agent

    with patch(
        "gideon.core.config.loader.AppConfig.load",
        return_value=_config_with_agents("gideon", "code-reviewer", "researcher"),
    ):
        name, err = _validate_agent("nonexistent")
        assert name == ""
        assert err
        assert "nonexistent" in err
        assert "code-reviewer" in err
        assert "researcher" in err


def test_unknown_agent_error_when_no_other_agents():
    """The typed error still fires with a placeholder when only reserved agents exist."""
    from gideon.engine.subagent import _validate_agent

    with patch(
        "gideon.core.config.loader.AppConfig.load",
        return_value=_config_with_agents("gideon"),
    ):
        name, err = _validate_agent("nonexistent")
        assert name == ""
        assert "nonexistent" in err
        assert "none configured" in err


def test_empty_input_returns_empty():
    from gideon.engine.subagent import _validate_agent

    name, err = _validate_agent("")
    assert name == ""
    assert err == ""
