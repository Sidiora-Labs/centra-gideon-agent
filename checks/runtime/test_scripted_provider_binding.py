"""PHF-7 — the seam that BINDS the offline scripted provider, and its guardrails.

Four properties of ``gideon.integrations.llm.registry``'s scripted registration:

1. With the opt-in ABSENT the type is not registered at all — the registry's type
   set is identical to a fresh one, asserted against a NON-EMPTY baseline so
   "unchanged" cannot pass vacuously.
2. With the opt-in PRESENT the type registers once, stays idempotent across
   repeated ``sync_entries_from_config()`` calls (its docstring claims idempotence,
   so it is proven rather than trusted), and ``build()`` returns the provider with
   NO credential.
3. A real, credential-declaring type STILL raises ``CredentialMissing`` under the
   same opt-in. A credential-free provider is a security-adjacent seam; this is the
   rail that stops it becoming an auth hole, and it goes through the production
   credential helper rather than a hand-rolled imitation of it.
4. The declared capability set is exactly the honest minimum, asserted as an
   EQUALITY so a later widening reds instead of sliding in.

``gideon.integrations.llm.scripted`` is a sibling deliverable of the same atom and is not
on this branch. These tests inject a stub module into ``sys.modules`` under its
real name: that lets the suite run standalone AND proves the production code really
imports that symbol from that module (a wrong module path would fail here).
"""

import json
import pathlib
import re
import sys
import types

import pytest

import gideon.integrations.llm as _llm_pkg
from gideon.integrations.llm.capabilities import Capability, ProviderCapability
from gideon.integrations.llm.registry import (
    SCRIPTED_PROVIDER_CAPABILITY,
    SCRIPTED_PROVIDER_ENTRY_NAME,
    SCRIPTED_PROVIDER_ENV,
    SCRIPTED_PROVIDER_MODEL,
    SCRIPTED_PROVIDER_TYPE,
    CredentialMissing,
    ProviderEntry,
    ProviderRegistry,
    ProviderResolutionError,
    get_default_registry,
    reset_default_registry,
    scripted_provider_enabled,
    sync_entries_from_config,
)


class _StubScriptedProvider:
    """Stands in for the sibling's ``ScriptedProvider``.

    Mirrors the real fixture's constructor EXACTLY: it takes nothing. The real
    ``ScriptedProvider.__init__`` is ``(self)`` on purpose — a ``script_path`` kwarg
    would be a hole in its env gate, and its own test pins ``inspect.signature`` to
    ``["self"]``. This stub previously accepted a keyword-only ``model``, mirroring a
    factory call that did not typecheck against the real class; keeping the two in step
    is the whole point of a stub, and `ignore_missing_imports` means nothing else checks
    it. It deliberately accepts NO credential argument either — a scripted fixture that
    could take one would blur the exemption these tests fence.
    """

    def __init__(self) -> None:
        self.model = ""


@pytest.fixture
def stub_scripted_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a stub ``gideon.integrations.llm.scripted`` for the duration of one test."""
    mod = types.ModuleType("gideon.integrations.llm.scripted")
    mod.ScriptedProvider = _StubScriptedProvider  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gideon.integrations.llm.scripted", mod)
    monkeypatch.setattr(_llm_pkg, "scripted", mod, raising=False)
    return mod


_REAL_TYPE = "credentialed_real"

_REAL_CAPABILITY = ProviderCapability(
    type=_REAL_TYPE,
    capabilities=frozenset({Capability.CHAT}),
    supports_streaming=True,
    supports_tools=True,
    supports_embeddings=False,
    supports_vision=False,
    max_context_tokens=0,
    notes="stands in for an installed vendor model app",
)


def _real_factory(
    *, entry: ProviderEntry, session_key: str | None = None, **kwargs: object
):
    """A real provider's factory, refusing through the PRODUCTION credential path.

    ``llm.branded_specs.resolve_credential`` is the one helper every shipped
    model-app factory calls, and it is what raises ``CredentialMissing`` — so this
    stand-in exercises the real refusal instead of imitating it.
    """
    import gideon.sdk.model  # noqa: F401
    from gideon.integrations.llm.branded_specs import resolve_credential

    resolve_credential(entry, kwargs, label=_REAL_TYPE)
    return _StubScriptedProvider(model=entry.model)


def _seed_real_type(registry: ProviderRegistry) -> None:
    """Register the stand-in real type.

    Doubles as the VACUITY FLOOR for the opt-in-absent test: it is what makes the
    "type set unchanged" comparison run against a non-empty set.
    """
    registry.register_type(_REAL_CAPABILITY, _real_factory)


def _registered_types(registry: ProviderRegistry) -> set[str]:
    """The registry exposes no public type-set accessor (``capability_of`` answers
    one type at a time), so read the mapping directly — the same access
    ``sync_entries_from_config`` itself takes on ``_entries``."""
    return set(registry._capabilities)


def _write_config(providers: list[dict]) -> None:
    """Write a ``config.json`` at the path production reads, under the fake home."""
    from gideon.core.config.loader import config_path

    config_path().write_text(json.dumps({"providers": providers}), encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolated_registry_and_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """No shared registry state, no real home, no inherited opt-in.

    ``reset_default_registry()`` runs on BOTH sides: this suite runs under xdist
    beside everything else, and a leaked ``scripted`` type would make an unrelated
    test's provider resolution pick up a fixture.
    """
    monkeypatch.delenv(SCRIPTED_PROVIDER_ENV, raising=False)
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    reset_default_registry()
    yield
    reset_default_registry()


@pytest.fixture
def opt_in(monkeypatch: pytest.MonkeyPatch, tmp_path) -> str:
    """Set the one explicit opt-in, naming a script path."""
    script = tmp_path / "chat-script.json"
    monkeypatch.setenv(SCRIPTED_PROVIDER_ENV, str(script))
    return str(script)


def test_opt_in_absent_registers_no_scripted_type_or_entry():
    registry = get_default_registry()
    _seed_real_type(registry)
    baseline = _registered_types(registry)

    assert (
        baseline
    ), "baseline type set is empty — the unchanged assertion below would be vacuous"

    assert scripted_provider_enabled() is False
    assert sync_entries_from_config() == 0

    assert _registered_types(registry) == baseline
    assert SCRIPTED_PROVIDER_TYPE not in _registered_types(registry)
    assert [e.name for e in registry.list_entries()] == []

    with pytest.raises(ProviderResolutionError):
        registry.capability_of(SCRIPTED_PROVIDER_TYPE)


def test_opt_in_absent_leaves_a_configured_real_provider_untouched():
    """The absent-opt-in path must not perturb the ordinary config sync at all."""
    _write_config(
        [{"name": "Real", "type": _REAL_TYPE, "model": "m", "credential": "real-key"}]
    )
    registry = get_default_registry()
    _seed_real_type(registry)

    assert sync_entries_from_config() == 1
    assert [e.name for e in registry.list_entries()] == ["Real"]
    assert registry.get_entry("Real").credential == "real-key"


def test_opt_in_present_registers_the_type_once_and_stays_idempotent(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    registry = get_default_registry()

    assert scripted_provider_enabled() is True
    assert sync_entries_from_config() == 1

    assert SCRIPTED_PROVIDER_TYPE in _registered_types(registry)
    assert (
        registry.capability_of(SCRIPTED_PROVIDER_TYPE) is SCRIPTED_PROVIDER_CAPABILITY
    )
    assert [e.name for e in registry.list_entries()] == [SCRIPTED_PROVIDER_ENTRY_NAME]

    assert sync_entries_from_config() == 1
    assert sync_entries_from_config() == 1
    assert [e.name for e in registry.list_entries()] == [SCRIPTED_PROVIDER_ENTRY_NAME]
    assert len(_registered_types(registry)) == 1


def test_build_returns_the_scripted_provider_with_no_credential(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    registry = get_default_registry()
    sync_entries_from_config()

    entry = registry.get_entry(SCRIPTED_PROVIDER_ENTRY_NAME)
    assert entry.type == SCRIPTED_PROVIDER_TYPE
    assert entry.credential is None

    provider = registry.build(SCRIPTED_PROVIDER_ENTRY_NAME)
    assert isinstance(provider, _StubScriptedProvider)

    assert (
        registry.get_entry(SCRIPTED_PROVIDER_ENTRY_NAME).model
        == SCRIPTED_PROVIDER_MODEL
    )

    assert isinstance(
        registry.build(SCRIPTED_PROVIDER_ENTRY_NAME, model="other-1"),
        _StubScriptedProvider,
    )


def test_the_entry_declares_the_capability_a_chat_turn_resolves_on(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    """Ties the declared set to the predicate real resolution actually applies.

    ``_resolve_from_config_registry`` picks the first entry whose declared
    capabilities contain ``_capability_enum(use_case)``; asserting against that
    function keeps this honest if the mapping ever changes.
    """
    from gideon.extensions.providers.provider_bridge import _capability_enum

    sync_entries_from_config()
    entry = get_default_registry().get_entry(SCRIPTED_PROVIDER_ENTRY_NAME)

    assert _capability_enum("chat") in entry.declared_capabilities


def test_the_chat_resolver_actually_returns_the_fixture_with_no_credential(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    """One level up from the registry: the production resolver returns it.

    ``_resolve_from_config_registry`` owns the implicit
    "first configured provider declaring the capability" fallback that applies when
    nothing is bound in ``active_models.json`` — which is the browser gate's state —
    and it is four of the five provider-returning paths in
    ``resolve_provider_for_use_case``. Asserting the entry merely *declares* chat
    would leave "and resolution picks it up" untested.
    """
    from gideon.extensions.providers.provider_bridge import (
        _resolve_from_config_registry,
    )

    sync_entries_from_config()

    resolved = _resolve_from_config_registry("chat")
    assert isinstance(resolved, _StubScriptedProvider)
    assert (
        get_default_registry().get_entry(SCRIPTED_PROVIDER_ENTRY_NAME).model
        == SCRIPTED_PROVIDER_MODEL
    )


def test_a_real_type_still_raises_credential_missing_under_the_opt_in(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    """The rail that stops a credential-free fixture becoming an auth hole.

    The exemption must be scoped to the scripted type alone: a real entry keeps its
    ``credential`` verbatim through the sync, and building it still refuses through
    the production credential helper — in the SAME registry state in which the
    fixture builds with none.
    """
    _write_config(
        [{"name": "Real", "type": _REAL_TYPE, "model": "m", "credential": "real-key"}]
    )
    registry = get_default_registry()
    _seed_real_type(registry)

    assert sync_entries_from_config() == 2

    with pytest.raises(CredentialMissing):
        registry.build("Real")

    real = registry.get_entry("Real")
    assert real.credential == "real-key"

    assert registry.get_entry(SCRIPTED_PROVIDER_ENTRY_NAME).credential is None
    assert isinstance(
        registry.build(SCRIPTED_PROVIDER_ENTRY_NAME), _StubScriptedProvider
    )


def test_the_exemption_is_a_property_of_the_type_not_of_the_opt_in(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    """A second credentialed entry (no scripted involvement) also still refuses.

    Guards the shape of the mistake: an exemption keyed on "the opt-in is set"
    rather than "this type is scripted" would exempt every entry synced while the
    gate runs.
    """
    _write_config(
        [
            {"name": "RealOne", "type": _REAL_TYPE, "model": "m", "credential": "k1"},
            {"name": "RealTwo", "type": _REAL_TYPE, "model": "m", "credential": "k2"},
        ]
    )
    registry = get_default_registry()
    _seed_real_type(registry)
    sync_entries_from_config()

    for name in ("RealOne", "RealTwo"):
        with pytest.raises(CredentialMissing):
            registry.build(name)
        assert registry.get_entry(name).credential


def test_declared_capabilities_are_exactly_the_minimum_set(
    opt_in: str, stub_scripted_module: types.ModuleType
):
    """Equality, not containment — a later widening must red here.

    CHAT because clause 1 is "completes a scripted chat turn"; CODE_TOOLS because
    the fixture's declared job includes tool-call emission. Nothing else: a JSON
    fixture performs none of the rest, and declaring one would make this entry the
    implicit fallback for a use case it cannot serve.
    """
    expected = frozenset({Capability.CHAT, Capability.CODE_TOOLS})

    assert SCRIPTED_PROVIDER_CAPABILITY.capabilities == expected

    sync_entries_from_config()
    entry = get_default_registry().get_entry(SCRIPTED_PROVIDER_ENTRY_NAME)
    assert entry.declared_capabilities == expected

    assert SCRIPTED_PROVIDER_CAPABILITY.supports_streaming is False
    assert SCRIPTED_PROVIDER_CAPABILITY.supports_embeddings is False
    assert SCRIPTED_PROVIDER_CAPABILITY.supports_vision is False
    assert SCRIPTED_PROVIDER_CAPABILITY.supports_tools is True
    assert SCRIPTED_PROVIDER_CAPABILITY.type == SCRIPTED_PROVIDER_TYPE


def test_the_omitted_capabilities_are_named_so_the_omission_is_deliberate():
    """Each of these is a capability a scripted JSON fixture cannot perform."""
    for absent in (
        Capability.EMBEDDING,
        Capability.VISION,
        Capability.STREAMING,
        Capability.PLANNING,
        Capability.SUMMARIZATION,
        Capability.TOOL_APPROVAL,
    ):
        assert absent not in SCRIPTED_PROVIDER_CAPABILITY.capabilities


RETIRED_ENV_NAMES = frozenset({"GIDEON_SCRIPTED_LLM"})


def test_the_registry_and_the_fixture_name_the_SAME_env_var() -> None:
    """One switch for the pair, asserted rather than promised.

    `SCRIPTED_PROVIDER_ENV`'s docstring claims it is "the SAME variable ScriptedProvider
    itself requires". It was not: the two modules independently spelled
    `GIDEON_SCRIPTED_LLM` and `GIDEON_SCRIPTED_MODEL_SCRIPT`, so the pair could
    only ever be half-enabled — one variable registers a type whose factory then refuses to
    construct, the other builds nothing because no type is registered. Neither branch's
    suite could fail on its own.
    """
    from gideon.integrations.llm import registry as R
    from gideon.integrations.llm import scripted as S

    assert R.SCRIPTED_PROVIDER_ENV == S.SCRIPT_ENV_VAR, (
        "the registration gate and the fixture's own gate name different env vars, so the "
        "pair can be half-enabled and neither half looks broken in isolation"
    )


def test_the_harness_spells_the_env_var_the_SAME_way() -> None:
    """Playwright owns fixture activation; Make invokes that console harness.

    Both files must reject unknown switch spellings, including stale prose, while
    only the configuration that launches the gateway must set the live switch.
    """
    from gideon.integrations.llm import registry as R

    env = R.SCRIPTED_PROVIDER_ENV
    root = pathlib.Path(__file__).resolve().parents[2]
    harness_files = {
        rel: (root / rel).read_text(encoding="utf-8")
        for rel in ("apps/console/playwright.config.ts", "Makefile")
    }
    assert env in harness_files["apps/console/playwright.config.ts"]
    makefile = harness_files["Makefile"]
    assert re.search(r"(?m)^WEB_DIR\s*:?=\s*apps/console\s*$", makefile)
    recipe = re.search(r"(?m)^test-e2e:[^\n]*\n((?:\t[^\n]*\n)+)", makefile)
    assert recipe, "Makefile has no test-e2e recipe"
    assert re.search(
        r"(?m)^\tcd\s+\$\(WEB_DIR\)\s*&&\s*npx\s+playwright\s+test(?:\s|$)",
        recipe.group(1),
    ), "test-e2e must launch the console Playwright configuration"
    for rel, text in harness_files.items():
        unknown = {
            token
            for token in re.findall(r"GIDEON_SCRIPTED[A-Z_]*", text)
            if token not in {env, "GIDEON_SCRIPTED", *RETIRED_ENV_NAMES}
        }
        assert not unknown, (
            f"{rel} names {sorted(unknown)}, which is neither the live switch "
            f"({env}) nor a declared retirement"
        )


def test_no_retired_env_name_is_still_read_by_anything() -> None:
    """The floor under the allowlist above: a retirement has to be real.

    Without this, `RETIRED_ENV_NAMES` is a way to make the drift sweep quiet — declare the
    stale name and the assertion goes green while a module still reads it. So the retirement is
    checked against the shipped source rather than trusted.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    src = root / "runtime"
    live = sorted(
        p.name
        for p in src.rglob("*.py")
        for n in RETIRED_ENV_NAMES
        if n in p.read_text(encoding="utf-8")
    )
    assert not live, (
        f"these shipped modules still name a RETIRED env var {sorted(RETIRED_ENV_NAMES)}: "
        f"{live}. It is not retired; either the allowlist is wrong or the module is."
    )
    assert (
        RETIRED_ENV_NAMES
    ), "the retirement list is empty — the sweep above allows nothing extra"


def test_the_factory_builds_the_REAL_fixture_through_the_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Build for real — a constructor mismatch is invisible to lint.

    The factory called `ScriptedProvider(model=...)` while the fixture's `__init__` takes
    only `self` — deliberately, because a `script_path` kwarg would be a hole in the env
    gate, and its own test pins `inspect.signature` to `["self"]`. That is a TypeError at
    bind time, and `ignore_missing_imports` meant mypy never mentioned it. So this asserts
    the built object, not the factory's source.
    """
    import json

    from gideon.integrations.llm.registry import (
        SCRIPTED_PROVIDER_ENTRY_NAME,
        get_default_registry,
        register_scripted_provider_type,
    )
    from gideon.integrations.llm.scripted import ScriptedProvider

    script = tmp_path / "chat-script.json"
    script.write_text(
        json.dumps(
            {
                "version": 1,
                "turns": [
                    {
                        "text": "Hello from the scripted provider.",
                        "stop_reason": "end_turn",
                        "usage": {
                            "input_tokens": 42,
                            "output_tokens": 9,
                            "cache_creation_tokens": 0,
                            "cache_read_tokens": 0,
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.setenv(SCRIPTED_PROVIDER_ENV, str(script))

    assert register_scripted_provider_type() is True
    registry = get_default_registry()
    built = registry.build(SCRIPTED_PROVIDER_ENTRY_NAME)
    assert isinstance(built, ScriptedProvider), type(built)
