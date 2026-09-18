"""REQ-2 — one embeddings control plane.

The memory API carried a SECOND embedding model-management surface next to
Settings → Models: ``GET /api/memory/embedding-status``,
``GET /api/memory/embedding-models``, ``POST /api/memory/enable-embeddings``,
``POST /api/memory/disable-embeddings``, ``POST /api/memory/activate-model`` and
``POST /api/memory/delete-model``, driven by a module-global
``_embedding_setup_status`` wizard state machine that no client ever read. The
one flow is ``PUT /api/models/active/{use_case}`` + ``POST
/api/models/embedding/reindex`` (Settings → Models).

``enable-embeddings`` also stamped ``memory.migrated = True`` in config.json
without moving a single row — a migration record for a migration that never
happened. The record now comes only from ``POST /api/memory/migrate``, and only
when the run actually produced data.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import gideon.interfaces.dashboard.handlers as handlers_pkg
import gideon.interfaces.dashboard.handlers.memory as mem_mod

_DASHBOARD = Path(mem_mod.__file__).resolve().parent.parent
_ADD_ROUTE = {
    "add_get": "GET",
    "add_post": "POST",
    "add_put": "PUT",
    "add_patch": "PATCH",
    "add_delete": "DELETE",
}

_REMOVED = {
    ("GET", "/api/memory/embedding-status"),
    ("GET", "/api/memory/embedding-models"),
    ("POST", "/api/memory/enable-embeddings"),
    ("POST", "/api/memory/disable-embeddings"),
    ("POST", "/api/memory/activate-model"),
    ("POST", "/api/memory/delete-model"),
}

_KEPT = {
    ("GET", "/api/models/active"),
    ("PUT", "/api/models/active/{use_case}"),
    ("GET", "/api/models/embedding/reindex"),
    ("POST", "/api/models/embedding/reindex"),
}


def _route_census() -> set[tuple[str, str]]:
    """Every literal route registered by the dashboard server + its handlers.

    An AST walk (no boot, no home) over ``server.py`` and ``handlers/*.py`` —
    the files that own the memory and model surfaces — collecting
    ``app.router.add_<method>("<literal>", ...)``.
    """
    files = [_DASHBOARD / "server.py", *sorted((_DASHBOARD / "handlers").glob("*.py"))]
    routes: set[tuple[str, str]] = set()
    for py in files:
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in _ADD_ROUTE:
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            path = node.args[0].value
            if isinstance(path, str):
                routes.add((_ADD_ROUTE[func.attr], path))
    return routes


class TestDuplicateControlsRemoved:
    def test_census_sees_the_real_surface(self) -> None:
        routes = _route_census()
        assert len(routes) > 200, "the AST census found almost nothing — it is vacuous"
        assert ("POST", "/api/memory/migrate") in routes

    def test_no_memory_level_embedding_route_survives(self) -> None:
        still_there = sorted(_REMOVED & _route_census())
        assert still_there == [], (
            "these duplicate memory-level embedding controls are registered again — "
            f"Settings → Models is the one flow: {still_there}"
        )

    def test_the_models_flow_is_the_one_that_remains(self) -> None:
        routes = _route_census()
        assert _KEPT <= routes, sorted(_KEPT - routes)

    def test_handler_package_exports_no_memory_embedding_controls(self) -> None:
        leaked = sorted(
            name
            for name in (
                "api_memory_embedding_status",
                "api_memory_embedding_models",
                "api_memory_enable_embeddings",
                "api_memory_disable_embeddings",
                "api_memory_activate_model",
                "api_memory_delete_model",
            )
            if hasattr(handlers_pkg, name) or hasattr(mem_mod, name)
        )
        assert leaked == [], leaked

    def test_setup_state_machinery_is_gone(self) -> None:
        src = Path(mem_mod.__file__).read_text(encoding="utf-8")
        for token in ("_embedding_setup_status", "setup_step", "can_retry"):
            assert token not in src, f"unused setup-state machinery is back: {token}"


class _Consolidator:
    def __init__(self) -> None:
        self._migrated = False


class _State:
    """Only what ``api_memory_migrate`` touches: the restricted-session set and
    the consolidator it flips once a migration really moved rows."""

    def __init__(self) -> None:
        self._restricted_keys: set[str] = set()
        self.consolidator = _Consolidator()


async def _migrate(store, state: _State) -> dict[str, int]:
    app = web.Application()
    app["state"] = state
    request = make_mocked_request("POST", "/api/memory/migrate", app=app)
    resp = await mem_mod.api_memory_migrate(request)
    assert resp.status == 200
    return json.loads(resp.body)


@pytest.fixture
def legacy_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "legacy"
    home.mkdir()
    monkeypatch.setattr(
        "gideon.cognition.vector_memory._path_home_gideon", lambda: home
    )
    return home


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from gideon.cognition.vector_memory import SemanticArchive

    archive = SemanticArchive(db_path=tmp_path / "memory.db")
    archive.init()
    monkeypatch.setattr(mem_mod, "_get_provider", lambda _state: archive)
    yield archive
    archive.close()


def _recorded() -> dict:
    from gideon.core.config.loader import config_path

    path = config_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("memory", {})


class TestMigrationRecordedOnlyWhenItMovedRows:
    """ac_3 — ``memory.migrated`` is a record of work done, not of a click."""

    @pytest.mark.asyncio
    async def test_a_no_op_migration_records_nothing(
        self, legacy_home: Path, store
    ) -> None:
        state = _State()

        counts = await _migrate(store, state)

        assert counts["semantic"] == 0 and counts["episodic"] == 0
        assert "migrated" not in _recorded(), (
            "a migration that moved nothing wrote a migration record — the home "
            "now claims a migration that never happened"
        )
        assert state.consolidator._migrated is False

    @pytest.mark.asyncio
    async def test_a_migration_that_moved_rows_records_it(
        self, legacy_home: Path, store
    ) -> None:
        (legacy_home / "lessons.jsonl").write_text(
            json.dumps({"rule": "prefer uv over pip", "category": "tool"}) + "\n",
            encoding="utf-8",
        )
        state = _State()

        counts = await _migrate(store, state)

        assert counts["semantic"] == 1
        assert [json.loads(row["value_json"]) for row in store.get_lessons()] == [
            "prefer uv over pip"
        ]
        assert _recorded()["migrated"] is True
        assert state.consolidator._migrated is True

    @pytest.mark.asyncio
    async def test_a_second_run_over_the_same_source_records_nothing_new(
        self, legacy_home: Path, store
    ) -> None:
        (legacy_home / "lessons.jsonl").write_text(
            json.dumps({"rule": "write tests first", "category": "knowledge"}) + "\n",
            encoding="utf-8",
        )
        assert (await _migrate(store, _State()))["semantic"] == 1

        from gideon.core.config.loader import config_path

        config_path().unlink()
        state = _State()

        counts = await _migrate(store, state)

        assert counts["semantic"] == 0
        assert "migrated" not in _recorded(), (
            "the re-run moved nothing (the rows were already there) and still "
            "wrote a fresh migration record"
        )
        assert state.consolidator._migrated is False
