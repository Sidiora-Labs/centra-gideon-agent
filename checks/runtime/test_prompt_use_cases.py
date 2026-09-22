"""Tests for prompt use-case bindings — which system prompt serves each context.

The default-agent system prompt resolves from the prompt provider via a per-use-case
binding (chat / background / code / goal_loop), falling back to the bundled
``system-default`` prompt (seeded from the shipped prompt) when unbound.
"""

import pytest

from gideon.extensions.providers import prompt_use_cases as puc


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    import gideon.integrations.prompt_providers.registry as reg

    reg._providers.clear()
    yield
    reg._providers.clear()


def test_use_case_vocabulary():
    for agent_uc in ("chat", "background", "code", "goal_loop"):
        assert agent_uc in puc.PROMPT_USE_CASES
    from gideon.integrations.prompt_providers.catalog import BUNDLED_PROMPTS

    assert puc.PROMPT_USE_CASES == tuple(p.use_case for p in BUNDLED_PROMPTS)


def test_unbound_falls_back_to_its_bundled_prompt():
    from gideon.integrations.prompt_providers.catalog import BUNDLED_PROMPTS

    for entry in BUNDLED_PROMPTS:
        assert puc.active_prompt_ref(entry.use_case) == f"native:{entry.name}"


def test_default_prompt_seeded_and_resolves():
    content = puc.resolve_prompt_content("chat")
    assert content and "Gideon" in content


def test_binding_overrides_resolution():
    from gideon.integrations.prompt_providers.base import PromptTemplate
    from gideon.integrations.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_prompt_provider,
    )

    _ensure_default_providers_registered()
    get_prompt_provider("native").create_prompt(
        PromptTemplate(
            name="custom-code", content="CUSTOM CODE SYSTEM PROMPT — long enough."
        )
    )
    puc.save_active_prompts({"code": "native:custom-code"})

    assert puc.active_prompt_ref("code") == "native:custom-code"
    assert puc.resolve_prompt_content("code").startswith("CUSTOM CODE")
    assert "Gideon" in puc.resolve_prompt_content("chat")


def test_declaration_keeps_the_template_and_snippets_on_the_bound_provider(tmp_path):
    from gideon.integrations.prompt_providers.base import PromptSnippet, PromptTemplate
    from gideon.integrations.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_prompt_provider,
    )

    _ensure_default_providers_registered()
    native = get_prompt_provider("native")
    assert native is not None
    native.create_prompt(
        PromptTemplate(
            name="custom-code",
            content="Agent {{bot_name}}: {{> bound-signature}}",
        )
    )
    native.create_snippet(PromptSnippet(name="bound-signature", content="ready"))
    puc.save_active_prompts({"code": "native:custom-code"})

    declaration = puc.resolve_prompt_declaration("code")
    assert declaration is not None
    assert declaration.provider is native
    assert declaration.template.name == "custom-code"

    from gideon.integrations.prompt_providers.runtime import render_use_case_prompt

    assert render_use_case_prompt("code", {"bot_name": "Ada"}) == "Agent Ada: ready"

    from gideon.cognition.context import PromptAssembler
    from gideon.cognition.memory import MemoryJournal
    from gideon.extensions.skills import ProcedureLibrary

    builder = PromptAssembler(
        memory=MemoryJournal(workspace=tmp_path),
        skills=ProcedureLibrary(
            skills_path=tmp_path / "skills", install_builtins=False
        ),
    )
    assert (
        builder._identity_prompt(None, "code:project", "chat", "", "")
        == "Agent Gideon: ready"
    )


def test_missing_bound_prompt_uses_its_declared_bundled_default():
    puc.save_active_prompts({"code": "native:removed"})

    declaration = puc.resolve_prompt_declaration("code")
    assert declaration is not None
    assert declaration.prompt_name == puc.bundled_prompt_name_for("code")
    assert declaration.template.name == "system-code"


def test_unknown_use_case_falls_back_to_chat_prompt():
    assert puc.active_prompt_ref("bogus") == f"native:{puc.DEFAULT_PROMPT_NAME}"
    assert puc.DEFAULT_PROMPT_NAME == "system-chat"


def test_save_rejects_unknown_use_case_keys():
    puc.save_active_prompts({"chat": "native:system-chat", "bogus": "native:x"})
    saved = puc.load_active_prompts()
    assert "chat" in saved and "bogus" not in saved


def test_split_ref():
    assert puc.split_ref("native:system-default") == ("native", "system-default")
    assert puc.split_ref("unqualified") is None


class TestSessionKeyDerivation:
    """The hot-path derives the use-case from the session_key when not explicit."""

    @pytest.mark.parametrize(
        "session_key,expected",
        [
            ("dashboard:abc", "chat"),
            ("cli_chat", "chat"),
            ("_bg", "background"),
            ("cron:job1", "background"),
            ("subagent:x", "background"),
            ("code:proj1", "code"),
            ("loop:goal1", "goal_loop"),
            ("campaign-7", "goal_loop"),
        ],
    )
    def test_derivation(self, session_key, expected):
        from gideon.cognition.context import _prompt_use_case_for

        assert _prompt_use_case_for(session_key) == expected

    def test_explicit_non_default_wins(self):
        from gideon.cognition.context import _prompt_use_case_for

        assert _prompt_use_case_for("dashboard:x", "code") == "code"


def test_every_core_use_case_has_a_human_label():
    assert len(puc.PROMPT_USE_CASES) >= 40
    for uc in puc.PROMPT_USE_CASES:
        label = puc.use_case_label(uc)
        assert label, f"{uc} has no label"
        if "_" in uc or "-" in uc:
            assert label != uc, f"{uc} still renders its raw key"
        assert not label.startswith(" ") and label.strip() == label


def test_every_core_use_case_has_a_hint():
    for uc in puc.PROMPT_USE_CASES:
        assert puc.use_case_hint(uc), f"{uc} has no description"


def test_the_four_agent_contexts_describe_the_CONTEXT_not_the_prompt():
    for uc in ("chat", "background", "code", "goal_loop"):
        assert "bundled Gideon system prompt" not in puc.use_case_hint(uc)
    assert puc.use_case_hint("chat") == "Interactive sessions — dashboard, Slack, CLI"


def test_every_core_use_case_lands_in_a_declared_category():
    for uc in puc.PROMPT_USE_CASES:
        cat = puc.use_case_category(uc)
        assert cat in puc.PROMPT_CATEGORY_ORDER, f"{uc} → unknown category {cat!r}"
        assert puc.PROMPT_CATEGORY_LABEL[cat] and puc.PROMPT_CATEGORY_HINT[cat]
    for cat in puc.PROMPT_CATEGORY_ORDER:
        assert any(puc.use_case_category(uc) == cat for uc in puc.PROMPT_USE_CASES), cat


def test_an_app_owned_use_case_is_named_and_described_like_a_bundled_one():
    from gideon.extensions.apps import prompt_registry

    prompt_registry.register_use_case(
        "widget_summarize",
        provider="native",
        prompt_name="task-widget-summarize",
        category="internal",
        app="native-widgets",
        description="Summarize a widget payload for the dashboard.",
    )
    try:
        assert "widget_summarize" in puc.all_prompt_use_cases()
        assert puc.use_case_label("widget_summarize") == "Widget summarize"
        assert (
            puc.use_case_hint("widget_summarize")
            == "Summarize a widget payload for the dashboard."
        )
        assert puc.use_case_category("widget_summarize") == "internal"
    finally:
        prompt_registry.unregister_app("native-widgets")


def test_an_app_declaring_a_junk_category_still_gets_a_row():
    from gideon.extensions.apps import prompt_registry

    prompt_registry.register_use_case(
        "odd_one",
        provider="native",
        prompt_name="task-odd",
        category="nonsense",
        app="a",
        description="",
    )
    try:
        assert puc.use_case_category("odd_one") == "internal"
        assert puc.use_case_label("odd_one") == "Odd one"
    finally:
        prompt_registry.unregister_app("a")
