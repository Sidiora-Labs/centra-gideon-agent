"""Refresh + registry-drift regressions (LOCAL-MODEL-MANAGER-V2 §11/§12, LMMV-7).

Two invariants that have each already broken once, locked with BEFORE/AFTER registration
evidence rather than a smoke check:

**(a) The two-population invariant (Success Criterion 9).** Every use-case registry holds
two populations: providers built from ``config.json`` on demand (remote adapters, the env
stub, scanner-contributed entries) and providers an APP contributed once through
``ModelTypeHandler`` on enable. ``refresh_providers()`` exists to re-read the FIRST
population; it must never touch the second. The original bug cleared the whole dict, which
silently unregistered the bundled backend until the next gateway restart. A count-only
assertion is not enough here, so every case asserts three things: the bundled provider is
STILL THE SAME OBJECT, the transient one is GONE (the vacuity guard — a ``refresh_providers``
that had become a no-op would otherwise pass), and the before/after totals move by exactly
the transient population's size.

**(b) Registry drift.** A sidecar/local model provider routed through
``ModelTypeHandler.register`` must land in the local-model registry under the APP name
(``ext.name``) — not under the provider's own ``.name``, which for a sidecar proxy is an
internal spelling (``faster_whisper`` vs the ``faster-whisper`` app) that no binding ref
uses. It must satisfy the ``is_local_model_provider`` duck-type on the way in, and it must
survive the use-case registry's ``refresh_providers()``. Drift in any one of the three
reads as "the model is not installed" in the UI while the provider is loaded in-process.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import gideon
from gideon.integrations.local_models import registry as lm_registry
from gideon.integrations.local_models.provider import LocalModel


class _Bundled:
    """An app-contributed provider: registered once on enable, must outlive a refresh."""

    def __init__(self, name: str = "bundled-backend") -> None:
        self.name = name
        self.display_name = "Bundled Backend"


class _Transient:
    """A config-built provider: exactly the population a refresh is allowed to drop."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.display_name = name


def _snapshot(mod) -> dict:
    return dict(mod._providers)


@pytest.mark.parametrize(
    "mod_path", ["gideon.integrations.stt.registry", "gideon.integrations.tts.registry"]
)
def test_refresh_keeps_the_bundled_population_remote_tracked(mod_path, monkeypatch):
    """stt / tts track their config-built adapters in ``_remote_names``; only those go."""
    import importlib

    mod = importlib.import_module(mod_path)
    bundled = _Bundled()
    transient = _Transient("openai")

    monkeypatch.setattr(mod, "_providers", {}, raising=False)
    monkeypatch.setattr(mod, "_remote_names", set(), raising=False)
    mod.register_provider(bundled)
    mod.register_provider(transient)
    mod._remote_names.add(transient.name)

    before = _snapshot(mod)
    assert len(before) == 2, before

    mod.refresh_providers()

    after = _snapshot(mod)
    assert mod.get_provider(bundled.name) is bundled
    assert mod.get_provider(transient.name) is None
    assert len(before) - len(after) == 1
    assert set(before) - set(after) == {transient.name}


def test_image_gen_refresh_keeps_a_manifest_bundle(monkeypatch):
    """image_gen names its transient population (OpenAI family + ``stub``) explicitly."""
    from gideon.extensions.providers import use_cases
    from gideon.integrations.image_gen import registry as ir

    monkeypatch.setattr(ir, "_providers", {}, raising=False)
    monkeypatch.setattr(use_cases, "openai_family_providers", lambda: [], raising=False)

    bundled = _Bundled("fal")
    stub = _Transient("stub")
    ir.register_provider(bundled)
    ir.register_provider(stub)

    before = _snapshot(ir)
    assert len(before) == 2, before

    ir.refresh_providers()

    after = _snapshot(ir)
    assert ir.get_provider("fal") is bundled
    assert ir.get_provider("stub") is None
    assert len(before) - len(after) == 1


def test_video_gen_refresh_keeps_a_manifest_bundle(monkeypatch):
    """video_gen tracks its scanner-contributed population in ``_scanner_names``."""
    from gideon.integrations.video_gen import registry as vr

    monkeypatch.setattr(vr, "_providers", {}, raising=False)
    monkeypatch.setattr(vr, "_scanner_names", set(), raising=False)

    bundled = _Bundled("fal")
    scanned = _Transient("scanned-provider")
    vr.register_provider(bundled)
    vr.register_provider(scanned)
    vr._scanner_names.add(scanned.name)

    before = _snapshot(vr)
    assert len(before) == 2, before

    vr.refresh_providers()

    after = _snapshot(vr)
    assert vr.get_provider("fal") is bundled
    assert vr.get_provider("scanned-provider") is None
    assert len(before) - len(after) == 1


def _modules_defining_refresh_providers() -> set[str]:
    """Every module in the shipped package that defines a module-level ``refresh_providers``.

    DISCOVERED from source rather than enumerated. The invariant is "a refreshing registry
    may only drop a NAMED population", and a hand-written list cannot state that about a
    registry nobody remembered to add to the list — the check would report clean for the one
    case it exists to catch. Cheap text prefilter, then an AST confirmation, so a mention in
    a docstring or a call site is never mistaken for a definition.
    """
    pkg_root = Path(gideon.__file__).resolve().parent
    found: set[str] = set()
    for path in pkg_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "def refresh_providers" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover — the lint gate owns syntax
            continue
        if not any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "refresh_providers"
            for n in tree.body
        ):
            continue
        rel = path.relative_to(pkg_root).with_suffix("")
        found.add(".".join(("gideon", *rel.parts)))
    return found


def test_every_use_case_registry_that_refreshes_declares_a_transient_population():
    """The invariant stated structurally: a registry may only drop a NAMED population.

    Any future ``refresh_providers()`` that clears ``_providers`` wholesale would have to
    delete one of these markers to pass, which is the point — the regression is that the
    drop was untargeted.

    The population it checks is DERIVED (see ``_modules_defining_refresh_providers``) and
    reconciled against this map in both directions, so a fifth registry that grows a
    ``refresh_providers`` is a failure here rather than a silent gap: it inherits the
    invariant on the day it is written, not on the day someone remembers this file.
    """
    import importlib

    markers = {
        "gideon.integrations.stt.registry": "_remote_names",
        "gideon.integrations.tts.registry": "_remote_names",
        "gideon.integrations.video_gen.registry": "_scanner_names",
        "gideon.integrations.image_gen.registry": "_auto_registered",
    }

    discovered = _modules_defining_refresh_providers()
    assert (
        len(discovered) >= 4
    ), f"the refresh_providers scan found only {sorted(discovered)}"
    assert "gideon.integrations.stt.registry" in discovered, sorted(discovered)

    undeclared = discovered - set(markers)
    assert not undeclared, (
        f"{sorted(undeclared)} define refresh_providers() but are not covered by the "
        f"two-population invariant (LMMV Success Criterion 9). Add each to this map with "
        f"the name of the transient population it is allowed to drop, and give it a case "
        f"above asserting the app-contributed population survives."
    )
    gone = set(markers) - discovered
    assert not gone, (
        f"{sorted(gone)} no longer define refresh_providers() — a rail entry matching "
        f"nothing looks clean. Drop the stale entries."
    )

    for mod_path, marker in markers.items():
        mod = importlib.import_module(mod_path)
        assert hasattr(mod, "refresh_providers"), mod_path
        assert hasattr(mod, marker), f"{mod_path} lost its transient-population marker"


class _SidecarProxy:
    """A sidecar proxy: the management contract, with an INTERNAL name that differs from
    the app's. Shaped exactly like the real ones (``faster_whisper`` for the
    ``faster-whisper`` app) because that mismatch is what the APP-name key exists for.
    """

    def __init__(self) -> None:
        self.name = "faster_whisper"
        self.display_name = "Faster Whisper (sidecar)"

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list[LocalModel]:
        return [LocalModel(name="small", capabilities=["stt"], context_tokens=0)]

    async def download_model(self, model_name: str) -> bool:
        return True

    async def delete_model(self, model_name: str) -> bool:
        return True


def _ext(name: str, capabilities: list[str]):
    from gideon.extensions.providers.registry import RegisteredProvider

    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.capabilities = capabilities
    rp = RegisteredProvider.__new__(RegisteredProvider)
    rp.name = name
    rp.provider_config = cfg
    return rp


def test_sidecar_proxy_satisfies_the_local_model_duck_type():
    """Gate one: the proxy is recognized WITHOUT subclassing ``LocalModelProvider``."""
    proxy = _SidecarProxy()
    assert lm_registry.is_local_model_provider(proxy, capabilities=["stt"]) is True
    assert (
        lm_registry.is_local_model_provider(proxy, capabilities=["image_gen"]) is False
    )


def test_model_type_handler_keys_a_sidecar_proxy_by_the_app_name(monkeypatch):
    """Gate two: the local-model registry key is ``ext.name``, not ``provider.name``.

    A binding ref is ``"<app>:<model>"``, so keying by the proxy's internal name strands
    every ref the UI writes — the model reads as not-installed while it is loaded.
    """
    from gideon.extensions.providers.registry import ModelTypeHandler

    monkeypatch.setattr(lm_registry, "_providers", {}, raising=False)
    monkeypatch.setattr(lm_registry, "_capabilities", {}, raising=False)

    proxy = _SidecarProxy()
    before = len(lm_registry._providers)

    ModelTypeHandler().register(_ext("faster-whisper", ["stt"]), proxy)

    assert len(lm_registry._providers) == before + 1
    assert lm_registry.get_provider("faster-whisper") is proxy
    assert lm_registry.get_provider("faster_whisper") is None
    assert lm_registry.capabilities_for("faster-whisper") == ["stt"]


def test_a_registered_sidecar_proxy_survives_a_use_case_refresh(monkeypatch):
    """Gate three: the two invariants meet — the proxy the handler registered is still
    there in BOTH registries after the use-case registry refreshes its config population.
    """
    from gideon.extensions.providers.registry import ModelTypeHandler
    from gideon.integrations.stt import registry as sr

    monkeypatch.setattr(lm_registry, "_providers", {}, raising=False)
    monkeypatch.setattr(lm_registry, "_capabilities", {}, raising=False)
    monkeypatch.setattr(sr, "_providers", {}, raising=False)
    monkeypatch.setattr(sr, "_remote_names", set(), raising=False)

    proxy = _SidecarProxy()
    ModelTypeHandler().register(_ext("faster-whisper", ["stt"]), proxy)
    sr.register_provider(proxy)
    sr.register_provider(_Transient("openai"))
    sr._remote_names.add("openai")

    local_before = len(lm_registry._providers)
    stt_before = len(sr._providers)

    sr.refresh_providers()

    assert len(lm_registry._providers) == local_before
    assert lm_registry.get_provider("faster-whisper") is proxy
    assert len(sr._providers) == stt_before - 1
    assert sr.get_provider("faster_whisper") is proxy
    assert sr.get_provider("openai") is None
