"""Local model discovery and staged provider binding for a seeded home."""

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)
DEFAULT_ENDPOINT = "http://localhost:11434"
ENDPOINT_ENV = "GIDEON_LOCAL_MODEL_ENDPOINT"
MODEL_ENV = "GIDEON_LOCAL_MODEL"
EMBEDDING_MODEL_ENV = "GIDEON_LOCAL_EMBEDDING_MODEL"
APPS_DIR_ENV = "GIDEON_LOCAL_MODEL_APPS_DIR"
PROVIDER_APP = "ollama-models"
PROVIDER_TYPE = "ollama"
PROVIDER_ENTRY_NAME = "Local Ollama"
PROBE_TIMEOUT_SECS = 2.0
REQUEST_TIMEOUT_SECS = 300
BOUND = "bound"
ALREADY_BOUND = "already_bound"
SKIPPED_NO_SERVER = "skipped_no_server"
SKIPPED_NO_MODEL = "skipped_no_model"
SKIPPED_NO_PROVIDER_APP = "skipped_no_provider_app"


@dataclass
class BindResult:
    status: str
    detail: str
    endpoint: str = ""
    model: str = ""
    embedding_model: str = ""
    provider_name: str = ""
    wrote: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {BOUND, ALREADY_BOUND}


def _probe_models(endpoint: str) -> list[dict] | None:
    address = f"{endpoint.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(address, timeout=PROBE_TIMEOUT_SECS) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as error:
        logger.debug("local model probe failed for %s: %s", address, error)
        return None
    records = data.get("models") if isinstance(data, dict) else None
    return records if isinstance(records, list) else []


def _by_recency(models: list[dict]) -> list[dict]:
    return sorted(
        models, reverse=True, key=lambda row: str(row.get("modified_at") or "")
    )


@dataclass(frozen=True)
class LocalCatalog:
    models: list[dict]

    def select(self, capability):
        from gideon.integrations.llm.catalog import infer_capabilities

        for row in _by_recency(self.models):
            identifier = str(row.get("model") or row.get("name") or "")
            if identifier:
                families = (
                    (row.get("details") or {}).get("families")
                    if isinstance(row, dict)
                    else None
                )
                families = families if isinstance(families, list) else None
                if capability in infer_capabilities(identifier, families):
                    return identifier
        return ""

    def contains(self, identifier):
        return identifier in {
            str(row.get("model") or row.get("name") or "") for row in self.models
        }


def _pick(models: list[dict], want: str) -> str:
    return LocalCatalog(models).select(want)


def _installed_provider_app() -> bool:
    from gideon.extensions.apps import manager

    try:
        return any(
            row.get("name") == PROVIDER_APP and row.get("enabled", True)
            for row in manager.list_apps()
        )
    except Exception:
        logger.debug("could not list installed apps", exc_info=True)
        return False


def _candidate_app_dirs(apps_dir: str | None) -> list[Path]:
    explicit = apps_dir or os.environ.get(APPS_DIR_ENV, "")
    candidates = [Path(explicit).expanduser()] if explicit else []
    try:
        from gideon.extensions.apps import catalog

        candidates.extend(
            Path(value).expanduser() for value in catalog.list_local_sources()
        )
        bundled = catalog._first_party_source()
        if bundled is not None:
            candidates.append(bundled)
    except Exception:
        logger.debug("local app-source discovery failed", exc_info=True)
    return candidates


def _resolve_app_source(apps_dir: str | None) -> Path | None:
    return next(
        (
            candidate
            for base in _candidate_app_dirs(apps_dir)
            for candidate in (base / PROVIDER_APP, base)
            if (candidate / "app.json").is_file() and candidate.name == PROVIDER_APP
        ),
        None,
    )


@dataclass(frozen=True)
class ProviderDocument:
    path: Path

    def read(self, fallback):
        try:
            return (
                json.loads(self.path.read_text(encoding="utf-8"))
                if self.path.exists()
                else {}
            )
        except (OSError, json.JSONDecodeError):
            return fallback

    def has(self, name):
        data = self.read({})
        entries = data.get("providers")
        return isinstance(entries, list) and any(
            isinstance(entry, dict) and entry.get("name") == name for entry in entries
        )

    def append(self, endpoint, model, embedding):
        from gideon.core.atomic_write import atomic_write

        data = self.read({})
        if not isinstance(data, dict):
            data = {}
        options = {
            "endpoint": endpoint,
            "default_model": model,
            "timeout_secs": REQUEST_TIMEOUT_SECS,
        }
        if embedding:
            options["embedding_model"] = embedding
        entries = data.setdefault("providers", [])
        if not isinstance(entries, list):
            data["providers"] = entries = []
        entries.append(
            {
                "name": PROVIDER_ENTRY_NAME,
                "type": PROVIDER_TYPE,
                "model": model,
                "options": options,
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(data, indent=2) + "\n", fsync=True)


def _config_has_entry(name: str) -> bool:
    from gideon.core.config.loader import config_path

    return ProviderDocument(config_path()).has(name)


def _write_provider_entry(*, endpoint: str, model: str, embedding_model: str) -> None:
    from gideon.core.config.loader import config_path

    ProviderDocument(config_path()).append(endpoint, model, embedding_model)


def _write_active_models(*, model: str, embedding_model: str) -> None:
    from gideon.extensions.providers.use_cases import (
        load_active_models,
        save_active_models,
    )

    active = load_active_models()
    pairs = [("chat", model)] + (
        [("embedding", embedding_model)] if embedding_model else []
    )
    active.update(
        {name: [f"{PROVIDER_ENTRY_NAME}:{identifier}"] for name, identifier in pairs}
    )
    save_active_models(active)


@dataclass(frozen=True)
class LocalBinding:
    endpoint: str
    model: str
    embedding: str
    apps_dir: str | None

    def run(self):
        endpoint = self.endpoint
        if _config_has_entry(PROVIDER_ENTRY_NAME):
            return BindResult(
                ALREADY_BOUND,
                f"a provider entry named {PROVIDER_ENTRY_NAME!r} already exists in config.json — leaving it alone",
                endpoint=endpoint,
                provider_name=PROVIDER_ENTRY_NAME,
            )
        models = _probe_models(endpoint)
        if models is None:
            return BindResult(
                SKIPPED_NO_SERVER,
                f"no Ollama answered {endpoint}/api/tags — the home is seeded but no "
                f"model is bound, so chat, approvals and artifacts stay empty. Start "
                f"Ollama and re-run, or set ${ENDPOINT_ENV} to a reachable endpoint.",
                endpoint=endpoint,
            )
        chat = self.model or _pick(models, "chat")
        if not chat:
            return BindResult(
                SKIPPED_NO_MODEL,
                f"{endpoint} is reachable but has no chat-capable model pulled — "
                f"nothing was bound. Pull one (e.g. `ollama pull llama3.2:3b`) and "
                f"re-run, or name one with ${MODEL_ENV}.",
                endpoint=endpoint,
            )
        embedding = self.embedding or _pick(models, "embedding")
        missing = not LocalCatalog(models).contains(chat)
        source = None
        if not _installed_provider_app():
            source = _resolve_app_source(self.apps_dir)
            if source is None:
                return BindResult(
                    SKIPPED_NO_PROVIDER_APP,
                    f"{endpoint} is reachable with model {chat!r}, but the "
                    f"{PROVIDER_APP!r} app is not installed in this home and no local "
                    f"app source has it. Nothing was written — a providers[] entry "
                    f"whose type no installed app registers is unbuildable. Install "
                    f"the Ollama app from the App Store, or point ${APPS_DIR_ENV} at a "
                    f"checkout of the apps repo.",
                    endpoint=endpoint,
                    model=chat,
                )
        return self.commit(chat, embedding, source, missing)

    def commit(self, chat, embedding, source, missing):
        written = []
        if source is not None:
            from gideon.extensions.apps import app_manager

            result = app_manager.install(
                source, origin="local", caller="seed_local_model"
            )
            if not result.ok:
                return BindResult(
                    SKIPPED_NO_PROVIDER_APP,
                    f"installing {PROVIDER_APP!r} from {source} failed "
                    f"({result.error or 'unknown error'}) — nothing was written.",
                    endpoint=self.endpoint,
                    model=chat,
                )
            written.append(f"apps/{PROVIDER_APP}/")
        _write_provider_entry(
            endpoint=self.endpoint, model=chat, embedding_model=embedding
        )
        written.append("config.json")
        _write_active_models(model=chat, embedding_model=embedding)
        written.append("active_models.json")
        detail = f"bound {PROVIDER_ENTRY_NAME!r} -> {chat} at {self.endpoint}"
        if embedding:
            detail += f" (embedding: {embedding})"
        if missing:
            detail += f" — note: {chat!r} is not pulled yet on this endpoint"
        return BindResult(
            BOUND, detail, self.endpoint, chat, embedding, PROVIDER_ENTRY_NAME, written
        )


def bind_local_model(
    *,
    endpoint: str | None = None,
    model: str | None = None,
    embedding_model: str | None = None,
    apps_dir: str | None = None,
) -> BindResult:
    return LocalBinding(
        (endpoint or os.environ.get(ENDPOINT_ENV, "") or DEFAULT_ENDPOINT).strip(),
        (model or os.environ.get(MODEL_ENV, "")).strip(),
        (embedding_model or os.environ.get(EMBEDDING_MODEL_ENV, "")).strip(),
        apps_dir,
    ).run()


def seed_local_model_cmd(args) -> int:
    result = bind_local_model(
        **{
            name: getattr(args, attribute, None)
            for name, attribute in (
                ("endpoint", "local_model_endpoint"),
                ("model", "local_model"),
                ("apps_dir", "local_model_apps_dir"),
            )
        }
    )
    print(
        f"seed-local-model: {result.status}: {result.detail}",
        file=sys.stdout if result.ok else sys.stderr,
    )
    return 0


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m gideon.operations.seed_local_model",
        description="Bind a local Ollama provider into $GIDEON_HOME. Writes nothing when no local Ollama is reachable.",
    )
    for flag, value in (("endpoint", "URL"), ("", "MODEL_ID"), ("apps-dir", "DIR")):
        parser.add_argument(
            "--local-model" + (f"-{flag}" if flag else ""), metavar=value, default=None
        )
    return seed_local_model_cmd(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(_main())
