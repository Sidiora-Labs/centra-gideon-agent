"""Real model-provider/session regressions for idle binding refresh."""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest


def _configure_local_ollama(tmp_path, monkeypatch, *, model="model-a", endpoint="http://127.0.0.1:9"):
    from gideon.core.config.loader import AppConfig, config_path
    from gideon.extensions.providers.loader import register_extension_providers
    from gideon.extensions.providers.provider_bridge import create_provider_factory
    from gideon.extensions.providers.use_cases import save_active_models
    from gideon.engine.session import ConversationDirectory

    home = tmp_path / "gideon-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    config_path().write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": "ollama-models",
                        "type": "ollama",
                        "model": model,
                        "options": {"endpoint": endpoint},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    # The bundled Ollama provider is an enabled native app. Exercise the same app
    # lifecycle that installs its provider type before replaying config entries.
    register_extension_providers()
    from gideon.integrations.llm.registry import sync_entries_from_config

    sync_entries_from_config()
    save_active_models({"chat": [f"ollama-models:{model}"]})
    directory = ConversationDirectory(
        AppConfig.load(), provider_factory=create_provider_factory("chat")
    )
    return directory


def _served_model(runtime) -> str:
    return str(runtime._model.model)


@pytest.mark.asyncio
async def test_open_real_chat_rebinds_at_next_idle_acquire(tmp_path, monkeypatch):
    from gideon.extensions.providers.use_cases import save_active_models

    directory = _configure_local_ollama(tmp_path, monkeypatch)
    try:
        first, _, _ = await directory.get_or_create("dashboard:rebind")
        assert _served_model(first) == "model-a"
        directory.release("dashboard:rebind")

        save_active_models({"chat": ["ollama-models:model-b"]})
        second, _, _ = await directory.get_or_create("dashboard:rebind")
        assert second is not first
        assert _served_model(second) == "model-b"
        directory.release("dashboard:rebind")
    finally:
        await directory.close_all()


@pytest.mark.asyncio
async def test_explicit_session_model_stays_pinned_across_chain_rebind(
    tmp_path, monkeypatch
):
    from gideon.extensions.providers.use_cases import save_active_models

    directory = _configure_local_ollama(tmp_path, monkeypatch, model="model-a")
    try:
        save_active_models(
            {"chat": ["ollama-models:model-a", "ollama-models:model-b"]}
        )
        first, _, _ = await directory.get_or_create(
            "dashboard:pinned", model="ollama-models:model-b"
        )
        assert _served_model(first) == "model-b"
        directory.release("dashboard:pinned")

        save_active_models(
            {"chat": ["ollama-models:model-c", "ollama-models:model-b"]}
        )
        second, _, _ = await directory.get_or_create("dashboard:pinned")
        assert second is not first
        assert _served_model(second) == "model-b"
        directory.release("dashboard:pinned")
    finally:
        await directory.close_all()


@pytest.mark.asyncio
async def test_inflight_lease_finishes_before_rebound_runtime_is_built(
    tmp_path, monkeypatch
):
    from gideon.extensions.providers.use_cases import save_active_models

    directory = _configure_local_ollama(tmp_path, monkeypatch)
    try:
        running, _, _ = await directory.get_or_create("dashboard:busy")
        assert _served_model(running) == "model-a"
        save_active_models({"chat": ["ollama-models:model-b"]})

        waiting = asyncio.create_task(directory.get_or_create("dashboard:busy"))
        await asyncio.sleep(0)
        assert not waiting.done()
        directory.release("dashboard:busy")
        rebound, _, _ = await asyncio.wait_for(waiting, timeout=3)
        assert rebound is not running
        assert _served_model(rebound) == "model-b"
        directory.release("dashboard:busy")
    finally:
        await directory.close_all()


@pytest.mark.asyncio
async def test_embedding_rebind_does_not_rebuild_chat_runtime(tmp_path, monkeypatch):
    from gideon.extensions.providers.use_cases import save_active_models

    directory = _configure_local_ollama(tmp_path, monkeypatch)
    try:
        first, _, _ = await directory.get_or_create("dashboard:chat")
        directory.release("dashboard:chat")
        save_active_models(
            {"chat": ["ollama-models:model-a"], "embedding": ["ollama-models:embed-b"]}
        )
        second, _, _ = await directory.get_or_create("dashboard:chat")
        assert second is first
        directory.release("dashboard:chat")
    finally:
        await directory.close_all()


@pytest.mark.asyncio
async def test_background_session_tracks_its_own_axis_and_chat_fallback(
    tmp_path, monkeypatch
):
    from gideon.engine.session import BACKGROUND_KEY
    from gideon.extensions.providers.use_cases import save_active_models

    directory = _configure_local_ollama(tmp_path, monkeypatch)
    try:
        await directory._ensure_background()
        first, _, _ = await directory.get_or_create(BACKGROUND_KEY)
        assert _served_model(first) == "model-a"
        directory.release(BACKGROUND_KEY)

        save_active_models({"chat": ["ollama-models:model-b"]})
        chat_fallback, _, _ = await directory.get_or_create(BACKGROUND_KEY)
        assert chat_fallback is not first
        assert _served_model(chat_fallback) == "model-b"
        directory.release(BACKGROUND_KEY)

        save_active_models(
            {"chat": ["ollama-models:model-b"], "background": ["ollama-models:model-c"]}
        )
        second, _, _ = await directory.get_or_create(BACKGROUND_KEY)
        assert second is not chat_fallback
        assert _served_model(second) == "model-c"
        directory.release(BACKGROUND_KEY)
    finally:
        await directory.close_all()


@pytest.mark.asyncio
async def test_provider_entry_model_and_endpoint_edits_refresh_implicit_runtime(
    tmp_path, monkeypatch
):
    from gideon.extensions.providers.use_cases import save_active_models
    from gideon.integrations.llm.registry import get_default_registry

    directory = _configure_local_ollama(tmp_path, monkeypatch, endpoint="http://127.0.0.1:9")
    registry = get_default_registry()
    try:
        save_active_models({})
        first, _, _ = await directory.get_or_create("dashboard:instance-edit")
        assert _served_model(first) == "model-a"
        assert first._model.endpoint == "http://127.0.0.1:9"
        directory.release("dashboard:instance-edit")

        current = registry.get_entry("ollama-models")
        registry.unregister_entry("ollama-models")
        registry.register_entry(
            dataclasses.replace(
                current,
                model="model-b",
                options={"endpoint": "http://127.0.0.1:10"},
            )
        )
        second, _, _ = await directory.get_or_create("dashboard:instance-edit")
        assert second is not first
        assert _served_model(second) == "model-b"
        assert second._model.endpoint == "http://127.0.0.1:10"
        directory.release("dashboard:instance-edit")
    finally:
        await directory.close_all()


def test_restored_stale_model_is_not_promoted_to_an_implicit_pin(tmp_path, monkeypatch):
    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.cognition.history import ConversationLog
    from gideon.interfaces.dashboard.chat_persistence import _rehydrate_session_from_history
    from gideon.interfaces.dashboard.state import ConsoleState

    directory = _configure_local_ollama(tmp_path, monkeypatch)
    log = ConversationLog(base_dir=tmp_path / "history")
    key = "dashboard:restored"
    log.append(key, "user", "A real stored message")
    log.update_metadata(key, {"model": "claude-3-sonnet", "title": "Restored"})
    state = ConsoleState(directory, 0, conversation_log=log)
    try:
        restored = _rehydrate_session_from_history(state, key)
        assert restored is not None
        assert restored.model == ""
    finally:
        # ConsoleState only owns session data; close the real provider directory.
        asyncio.run(directory.close_all())
