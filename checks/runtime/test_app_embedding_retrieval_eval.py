"""REQ-28 — a standalone evaluation resolves an INSTALLED APP's embedding provider.

``checks/runtime/test_cli_provider_bootstrap.py`` proves the bootstrap seam in
isolation: ``gideon retrieval-eval`` calls ``bootstrap_cli_providers()`` and that
imports an enabled installed app's provider module. It stops there — the module's
provider never reaches an evaluation, so nothing proved that the retrieval bench's
VECTOR arm comes back to life because an app contributed the embedder.

This drives the whole path in one process, twice over the same real stores:

1. with no provider app installed and the embedding use-case bound to a ref no
   provider answers, ``gideon retrieval-eval --store memory`` reports the vector
   arm as ``unmeasured`` — "no executor";
2. with a real installed app on disk (``app.json`` + ``installed.json`` + a
   provider module that subclasses :class:`EmbeddingProvider`) bound to the
   embedding use-case, the SAME command resolves that app's provider, calls it,
   and the vector arm runs and scores.

Everything is real: a real ``SemanticArchive`` under the tmp home with mined
``mem_volunteer_events`` qrels, the real ``gideon.interfaces.cli.main.main()``
argv dispatch, the real provider loader, and a real (deterministic, content-derived)
embedding function inside the app. No gateway is started and no app backend is
spawned — :func:`no_backend_processes` fails the test if either is attempted.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from gideon.assurance.evals import retrieval_bench as rb

_APP = "probe-embeddings"
_MODEL = "probe-mini"
_REF = f"{_APP}:{_MODEL}"
_UNRESOLVABLE = "no-such-embedder"

_RECORDS = {
    "project.orion.deploy_window": "Orion deploys land Tuesdays 10:00-12:00 UTC",
    "project.orion.oncall": "Orion pages route to the platform rotation",
    "project.postgres.vacuum": "Postgres autovacuum thresholds and the VACUUM FULL hatch",
    "project.terraform.state": "Terraform force-unlock recovers a locked state file",
    "project.nginx.tls": "Nginx prefers the ChaCha20 cipher suites on mobile",
    "pref.baking.hydration": "Sourdough at higher hydration needs a stiffer starter",
    "user.tool.ripgrep": "ripgrep needs the hidden flag to search dotfiles",
}

_VOLUNTEERED = (
    ("Orion", "project", ("project.orion.deploy_window", "project.orion.oncall")),
    ("Postgres", "tool", ("project.postgres.vacuum",)),
    ("Terraform", "tool", ("project.terraform.state",)),
    ("Nginx", "tool", ("project.nginx.tls",)),
    ("Sourdough", "topic", ("pref.baking.hydration",)),
    ("ripgrep", "tool", ("user.tool.ripgrep",)),
)

_PROVIDER_PY = '''"""A real embedding provider contributed by an installed app."""

import hashlib
import json
from pathlib import Path

from gideon.integrations.embedding_providers.base import (
    EmbeddingModel,
    EmbeddingProvider,
)

NAME = {app!r}
MODEL = {model!r}
DIM = 16
RECEIPT = Path({receipt!r})


def _vector(text):
    """A deterministic content-derived unit vector — hashed character trigrams."""
    body = text.lower()
    slots = [0.0] * DIM
    for start in range(max(0, len(body) - 2)):
        digest = hashlib.sha256(body[start : start + 3].encode("utf-8")).digest()
        slots[digest[0] % DIM] += 1.0
    norm = sum(value * value for value in slots) ** 0.5
    return [value / norm for value in slots] if norm else slots


class ProbeEmbeddings(EmbeddingProvider):
    @property
    def name(self):
        return NAME

    @property
    def display_name(self):
        return "Probe Embeddings"

    async def is_available(self):
        return True

    async def embed(self, text, model=""):
        calls = json.loads(RECEIPT.read_text("utf-8")) if RECEIPT.is_file() else []
        calls.append({{"model": model, "text": text}})
        RECEIPT.write_text(json.dumps(calls), encoding="utf-8")
        return _vector(text)

    async def embed_batch(self, texts, model=""):
        return [await self.embed(text, model) for text in texts]

    async def list_models(self):
        return [EmbeddingModel(name=MODEL, dimension=DIM, downloaded=True)]

    async def download_model(self, model):
        return True

    async def delete_model(self, model):
        return False


def create_provider(config=None):
    return ProbeEmbeddings()
'''


@pytest.fixture(autouse=True)
def no_backend_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A standalone evaluation must launch nothing: no app backend, no watchdog.

    The two spawn sites (``BackendSupervisor.start`` / ``WorkerSupervisor._spawn``)
    both go through ``subprocess.Popen``; the gateway tail of
    ``load_all_extensions`` is the other way a backend comes up.
    """
    from gideon.extensions.providers import loader

    def _refuse(*args: object, **kwargs: object):
        raise AssertionError(f"a standalone evaluation launched a process: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", _refuse)
    for name in (
        "start_enabled_app_backends",
        "start_backend_watchdog",
        "start_worker_watchdog",
        "start_sidecar_watchdog",
    ):
        if hasattr(loader, name):
            monkeypatch.setattr(loader, name, _refuse)


@pytest.fixture(autouse=True)
def clean_provider_registries():
    """The provider registries are process-global — undo everything this app added."""
    yield
    from gideon.extensions.apps.native_contract import namespaced_module_name
    from gideon.extensions.providers.registry import (
        get_provider_registry,
        reset_provider_registry,
    )
    from gideon.integrations.embedding_providers.registry import unregister_provider
    from gideon.integrations.local_models import registry as local_registry

    try:
        get_provider_registry().deregister(_APP)
    except Exception:  # noqa: BLE001 - teardown must not mask the test's own failure
        pass
    reset_provider_registry()
    unregister_provider(_APP)
    local_registry._providers.pop(_APP, None)
    local_registry._capabilities.pop(_APP, None)
    sys.modules.pop(namespaced_module_name(_APP, "provider"), None)


@pytest.fixture()
def seeded_memory_store() -> None:
    """A real ``SemanticArchive`` under the tmp home with mined volunteer qrels.

    Mirrors the corpus ``checks/runtime/test_retrieval_bench.py`` builds: records,
    an entity, volunteered-then-used references (the ``mem_volunteer_events``
    source :func:`rb.mine_memory_qrels` reads) and the recall bumps that make them
    positives.
    """
    from gideon.cognition.vector_memory import SemanticArchive

    store = SemanticArchive()
    store.init()
    for key, value in _RECORDS.items():
        store.set_semantic(key, value, 0.9, "test")
    graph = store.graph
    for name, kind, refs in _VOLUNTEERED:
        entity = graph.upsert_entity(name, kind)
        for ref in refs:
            row = store.db.execute(
                "SELECT recall_count FROM semantic_memory WHERE key = ?", (ref,)
            ).fetchone()
            graph.log_volunteer(
                entity_id=entity,
                entity_name=name,
                arm="alias",
                confidence=0.9,
                from_kind="semantic",
                record_ref=ref,
                recall_at_volunteer=int((row["recall_count"] if row else 0) or 0),
            )
            graph.add_link(
                from_kind="semantic",
                from_ref=ref,
                link_type="mentions",
                to_entity=entity,
                source="test",
            )
    store.record_recall(list(_RECORDS))
    store.db.commit()
    store.close()


def _bind(embedding_ref: str) -> None:
    from gideon.extensions.providers.use_cases import save_active_models

    save_active_models({"chat": ["test-chat"], "embedding": [embedding_ref]})


def _install_app(receipt: Path) -> Path:
    """Write a real installed provider app into the tmp home's apps dir."""
    from gideon.extensions.apps.manager import apps_dir

    app_dir = apps_dir() / _APP
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "name": _APP,
                "version": "1.0.0",
                "displayName": "Probe Embeddings",
                "description": "test-only embedding provider app",
                "provider": {
                    "type": "model",
                    "providerType": "probe",
                    "implementation": "provider:create_provider",
                    "capabilities": ["embedding"],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (app_dir / "installed.json").write_text(
        json.dumps(
            {
                "name": _APP,
                "version": "1.0.0",
                "enabled": True,
                "origin": "local",
                "lifecycle": "gateway",
                "resources": "gateway",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (app_dir / "provider.py").write_text(
        _PROVIDER_PY.format(app=_APP, model=_MODEL, receipt=str(receipt)),
        encoding="utf-8",
    )
    return app_dir


def _run_retrieval_eval(monkeypatch: pytest.MonkeyPatch) -> str:
    """The REAL command path: ``gideon retrieval-eval --store memory``."""
    from gideon.interfaces.cli import main as cli_main

    monkeypatch.setattr(sys, "argv", ["gideon", "retrieval-eval", "--store", "memory"])
    cli_main.main()
    return rb.latest_bench_id(rb.STORE_MEMORY)


def _artifacts(bench_id: str) -> tuple[dict, dict]:
    table = rb.read_bench_artifact(bench_id, "table.json")
    contributions = rb.read_bench_artifact(bench_id, "contributions.json")
    assert isinstance(table, dict) and isinstance(contributions, list)
    return table, {c["arm"]: c for c in contributions}


class TestAnInstalledAppRestoresTheVectorArm:
    def test_without_the_app_the_vector_arm_has_no_executor(
        self, seeded_memory_store, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        _bind(_UNRESOLVABLE)

        bench_id = _run_retrieval_eval(monkeypatch)

        table, contributions = _artifacts(bench_id)
        assert table["arm_executors"][rb.ARM_VECTOR] is False
        assert contributions[rb.ARM_VECTOR]["verdict"] == rb.ARM_UNMEASURED
        assert any(
            "no executor" in reason
            for reason in contributions[rb.ARM_VECTOR]["reasons"]
        )
        assert "no executor for: vector" in capsys.readouterr().out
        assert table["arm_executors"][rb.ARM_KEYWORD] is True, (
            "the keyword arm must still run — otherwise this says nothing about "
            "the embedder"
        )

    def test_the_evaluation_resolves_the_installed_app_provider(
        self, seeded_memory_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ac_1 — the app's own module answered the evaluation's embed calls."""
        receipt = tmp_path / "embed_calls.json"
        _install_app(receipt)
        _bind(_REF)
        assert not receipt.exists()

        _run_retrieval_eval(monkeypatch)

        assert receipt.is_file(), (
            "the standalone evaluation never reached the installed app's embedding "
            "provider — its module was not imported, or the ref did not resolve"
        )
        calls = json.loads(receipt.read_text(encoding="utf-8"))
        assert calls, "the app's provider was resolved but never asked to embed"
        assert {call["model"] for call in calls} == {
            _MODEL
        }, "the evaluation called the app provider with the wrong model id"

        from gideon.integrations.embedding_providers.registry import get_provider

        provider = get_provider(_APP)
        assert provider is not None
        assert type(provider).__module__.endswith("__provider"), (
            f"the resolved provider came from {type(provider).__module__!r}, not the "
            "installed app's module"
        )

    def test_the_installed_app_restores_the_vector_arm(
        self,
        seeded_memory_store,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys,
    ) -> None:
        """ac_2 — the vector arm goes from "no executor" to measured and scored."""
        _install_app(tmp_path / "embed_calls.json")
        _bind(_REF)

        bench_id = _run_retrieval_eval(monkeypatch)

        table, contributions = _artifacts(bench_id)
        assert table["arm_executors"][rb.ARM_VECTOR] is True
        assert contributions[rb.ARM_VECTOR]["verdict"] != rb.ARM_UNMEASURED
        assert "no executor" not in capsys.readouterr().out

        solo = next(
            row
            for row in table["rows"]
            if row["mask"] == rb.mask_name((rb.ARM_VECTOR,))
        )
        assert solo["no_candidate_queries"] == 0, (
            "the vector arm ran but returned no candidate for some query — it is "
            f"still dead there: {solo}"
        )
        assert solo["scored_queries"] == solo["queries"] >= rb.MIN_SCORED_QUERIES
        assert solo["p_at_k"] is not None
        assert contributions[rb.ARM_VECTOR]["scored_queries"] >= rb.MIN_SCORED_QUERIES
        assert not any(
            "no executor" in reason
            for reason in contributions[rb.ARM_VECTOR]["reasons"]
        )

    def test_the_run_launches_no_backend_process(
        self, seeded_memory_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The no-process rail is armed for real: the run completes under it."""
        _install_app(tmp_path / "embed_calls.json")
        _bind(_REF)

        bench_id = _run_retrieval_eval(monkeypatch)

        assert bench_id, "the evaluation produced no run"
        with pytest.raises(AssertionError, match="launched a process"):
            subprocess.Popen(["/bin/true"])
