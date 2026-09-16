"""The local-model management contract — descriptor + provider ABC.

A provider that owns local downloadable models implements this so core can list,
download, and delete them uniformly, and surface them for use-case binding. It is the
*management* axis only; a provider ALSO subclasses its use-case ABC (SttProvider,
TtsProvider, …) for the *inference* axis. The two are orthogonal by design: management
is identical across use-cases, inference is not.
"""

from __future__ import annotations

import json
import logging
import platform
import sys
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ARCH_ALIASES: dict[str, str] = {"aarch64": "arm64", "amd64": "x86_64", "x64": "x86_64"}

_TRUNCATION_FLOOR = 0.60


def host_platform_token() -> str:
    """The host's ``<platform>-<arch>`` token for catalog platform filtering.

    ``darwin-arm64``, ``linux-x86_64``, … — the same spelling a catalog entry's
    ``platforms`` list uses, so :meth:`LocalModelProvider._models_from_catalog` can keep
    an entry only on the hosts it names. Arch aliases are normalized (:data:`_ARCH_ALIASES`).
    """
    machine = platform.machine().lower()
    return f"{sys.platform}-{_ARCH_ALIASES.get(machine, machine)}"


_NON_COMMERCIAL_MARKERS: tuple[str, ...] = (
    "-nc",
    "cc-by-nc",
    "noncommercial",
    "non-commercial",
)


def _is_non_commercial(license_id: str, explicit: Any = None) -> bool:
    """Whether a license is non-commercial — explicit card flag wins, else an SPDX sniff.

    A card may say ``non_commercial: true`` outright; otherwise the SPDX id is checked for
    the usual NC markers (``CC-BY-NC-4.0``, ``*-NC``, …). Advisory only — the model is
    never blocked, just chipped.
    """
    if explicit is not None:
        return bool(explicit)
    low = license_id.lower()
    return any(marker in low for marker in _NON_COMMERCIAL_MARKERS)


def _matrix_from_dict(data: dict[str, Any]) -> CapabilityMatrix:
    """Build a :class:`CapabilityMatrix` from a card's ``matrix`` object, keeping only the
    keys the dataclass declares (an unknown key in the file is ignored, not an error).
    """
    known = {f.name for f in fields(CapabilityMatrix)}
    return CapabilityMatrix(**{k: v for k, v in data.items() if k in known})


@dataclass
class CapabilityMatrix:
    """Per-model feature flags a binding UI renders as chips instead of guessing.

    Optional on :class:`LocalModel` (default ``None`` = "unknown, don't assert"). Kept a
    flat, extensible dataclass on purpose — MI-2 added the cloning / voice-design flags
    below — so a new capability is one field, not a schema migration.
    """

    word_timestamps: bool = False
    segment_timestamps: bool = False
    speaker_labels: bool = (
        False  # a joint transcribe+diarize model fills `speaker` at source
    )
    acoustic_events: bool = False
    hotword_biasing: bool = False
    hotword_budget: int = 0
    languages: list[str] = field(default_factory=list)
    reasoning_budget_control: bool = False
    supports_cloning: bool = False
    supports_voice_design: bool = False


@dataclass
class LocalModel:
    """One model a local provider offers — everything the download UI + binding need.

    A provider RETURNS these from :meth:`LocalModelProvider.list_models`; core stores
    no per-model knowledge of its own. ``capabilities`` names the use-cases the model
    serves (``["stt"]``, ``["chat", "embedding"]``, …) so it appears under the right
    use-case in Settings → Models and the runtime can bind + inference against it.
    """

    name: str
    size_mb: float = 0
    description: str = ""
    downloaded: bool = False
    capabilities: list[str] = field(default_factory=list)
    gated: bool = False
    source: str = ""
    matrix: CapabilityMatrix | None = None
    runtime: str = ""
    runtime_contract: str = ""
    license: str = ""  # SPDX id, normalized never rejected (omnivoice rule)
    non_commercial: bool = False
    context_tokens: int = 0
    output_tokens: int = 0
    io_mime: dict = field(default_factory=dict)
    status: str = "active"
    integrity: str = ""
    config_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "id": self.name,
            "size_mb": self.size_mb,
            "size": int(self.size_mb or 0) * 1024 * 1024,
            "description": self.description,
            "downloaded": self.downloaded,
            "capabilities": list(self.capabilities),
            "gated": self.gated,
            "source": self.source,
            "matrix": asdict(self.matrix) if self.matrix is not None else None,
            "runtime": self.runtime,
            "runtime_contract": self.runtime_contract,
            "license": self.license,
            "non_commercial": self.non_commercial,
            "context_tokens": self.context_tokens,
            "output_tokens": self.output_tokens,
            "io_mime": dict(self.io_mime),
            "status": self.status,
            "integrity": self.integrity,
            "config_only": self.config_only,
        }


class LocalModelProvider(ABC):
    """A provider that owns locally-downloadable models.

    The management contract. Implementers also subclass their use-case ABC for
    inference; core resolves *this* surface for download/delete/list + availability,
    and the use-case registry for inference. Duck-typed at the registration seam —
    any ``type: model`` app whose provider implements these methods is registered.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable registry key (the provider/app name)."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human label for the provider's download card."""
        ...

    searchable: bool = False

    @abstractmethod
    async def is_available(self) -> bool:
        """Whether this provider can run here (its runtime deps are importable)."""
        ...

    @abstractmethod
    async def list_models(self) -> list[LocalModel]:
        """The models to show in downloads — downloaded AND downloadable.

        A fixed-catalog provider returns its full known set. A :attr:`searchable`
        provider returns just the locally-present models here (discovery of the rest
        goes through :meth:`search_models`)."""
        ...

    async def search_models(self, query: str) -> list[LocalModel]:
        """Search a dynamic remote catalog for installable models (``searchable`` only).

        Default: no remote catalog → empty. Overridden by ollama to scrape its library.
        """
        return []

    @abstractmethod
    async def download_model(self, model_name: str) -> bool:
        """Fetch a model's weights locally. Returns True on success.

        A gated model with no token configured returns False (the UI greys it until a
        token is set). Long fetches run inside the download-job runner off the loop.
        """
        ...

    @abstractmethod
    async def delete_model(self, model_name: str) -> bool:
        """Remove a downloaded model. Returns True on success (False if not present)."""
        ...

    def cache_dir(self) -> str | None:
        """The dir whose on-disk growth tracks a download (best-effort progress bar).

        None → the job runner falls back to the shared models root; progress degrades
        to indeterminate rather than coupling core to a backend's cache layout.
        """
        return None

    _MODEL_ATTRS: tuple[str, ...] = ()

    _MODEL_ATTR_GUESSES: tuple[str, ...] = ("_model", "model", "_pipeline", "_models")

    def loaded_models(self) -> list[dict[str, Any]]:
        """The models this provider currently holds IN MEMORY (LMMV §7).

        ``[]`` means nothing is resident — which is different from "this provider has no
        models". The reflective default reads :attr:`_MODEL_ATTRS` (or the conventional
        names) and reports every non-None one, so a provider gets an honest answer without
        implementing anything; a provider that knows better overrides this.

        Each row is ``{"model": str, "attr": str}``; the caller adds provider, kind and
        RSS attribution. Never raises — an unreadable attribute is simply not resident.
        """
        names = self._MODEL_ATTRS or self._MODEL_ATTR_GUESSES
        rows: list[dict[str, Any]] = []
        for attr in names:
            try:
                value = getattr(self, attr, None)
            except (
                Exception
            ):  # noqa: BLE001 — a property that raises is not a resident model
                continue
            if value is None:
                continue
            if isinstance(value, dict):
                rows.extend(
                    {"model": str(k), "attr": attr}
                    for k, v in value.items()
                    if v is not None
                )
                continue
            rows.append(
                {"model": getattr(value, "name", "") or attr.lstrip("_"), "attr": attr}
            )
        return rows

    def unload(self) -> bool:
        """Release resident models. Idempotent — True if anything was actually freed.

        The default drops every attribute :meth:`loaded_models` reports, which is what
        makes RSS available for reclaim; a provider holding its model somewhere subtler
        overrides. Calling it twice is not an error, and calling it on a provider holding
        nothing returns False rather than pretending.
        """
        freed = False
        for attr in self._MODEL_ATTRS or self._MODEL_ATTR_GUESSES:
            if getattr(self, attr, None) is None:
                continue
            try:
                setattr(self, attr, None)
                freed = True
            except (
                Exception
            ):  # noqa: BLE001 — a read-only attribute simply can't be freed
                logger.debug("unload could not clear %s.%s", type(self).__name__, attr)
        return freed

    async def ensure_ready(self) -> tuple[bool, str]:
        """``(ok, state)`` where state is ``ready`` / ``loading`` / ``unavailable``.

        Separates the LOAD budget from the INFERENCE budget: a provider paging a
        multi-gigabyte model in from disk is ``loading``, not hung, and the surface can say
        so instead of showing a spinner that looks like a bug. The default answers from
        :meth:`is_available` (never ``loading``, because a provider that doesn't implement
        warming has no warming state to report).
        """
        try:
            ok = await self.is_available()
        except (
            Exception
        ):  # noqa: BLE001 — availability must never raise into the surface
            return False, "unavailable"
        return (True, "ready") if ok else (False, "unavailable")

    def _models_from_catalog(
        self,
        catalog_path: Path,
        *,
        cache_root: Path | None = None,
    ) -> list[LocalModel]:
        """Build the model list from a declarative ``catalog.json`` (LMMV §2.3).

        A fixed-catalog provider's :meth:`list_models` becomes a one-liner over this: the
        catalog file is the source of truth, so adding or deprecating a model is a file
        drop, not a code change. Fail-soft like ``registry.catalog_for`` — a missing or
        malformed file logs a warning and yields ``[]``, and one bad card is skipped
        rather than blanking the whole list.

        **That one-liner carries the DISK verdict too**, and it did not use to. ``cache_root``
        was an opt-in keyword that skipped :meth:`_apply_disk_state` entirely when omitted,
        and the one shipped adopter omitted it — so ``downloaded`` read False for weights
        sitting right there, and ``integrity`` (whose only writer is that method) was never
        set, which left the FE's truncated chip and its Repair button unreachable in every
        build. It now defaults to :func:`~gideon.integrations.local_models.layouts.cache_root_for`,
        the same resolution the download runner already uses. Pass it only to override.
        """
        from gideon.integrations.local_models import layouts

        try:
            raw = json.loads(catalog_path.read_text("utf-8"))
        except (OSError, ValueError):
            logger.warning("catalog unreadable at %s — no models listed", catalog_path)
            return []
        entries = raw.get("models", raw) if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            logger.warning("catalog at %s is not a list of model cards", catalog_path)
            return []

        host = host_platform_token()
        root = cache_root
        out: list[LocalModel] = []
        for card in entries:
            if not isinstance(card, dict):
                continue
            try:
                model = self._model_from_card(card, host)
            except Exception:  # noqa: BLE001 — one bad card must not blank the list
                logger.warning(
                    "skipping malformed model card in %s", catalog_path, exc_info=True
                )
                continue
            if model is None:
                continue
            if root is None:
                root = layouts.cache_root_for(self)
            self._apply_disk_state(model, root, layouts)
            out.append(model)
        return out

    @staticmethod
    def _model_from_card(card: dict[str, Any], host: str) -> LocalModel | None:
        """One catalog card → a :class:`LocalModel`, or None if this host is excluded.

        ``platforms`` absent or empty = every host; otherwise the host token
        (:func:`host_platform_token`) must appear. ``label`` maps to ``description``.
        """
        name = str(card.get("name") or "").strip()
        if not name:
            raise ValueError("model card missing 'name'")
        platforms = card.get("platforms") or []
        if platforms and host not in platforms:
            return None

        matrix_data = card.get("matrix")
        matrix = (
            _matrix_from_dict(matrix_data) if isinstance(matrix_data, dict) else None
        )
        license_id = str(card.get("license") or "")
        return LocalModel(
            name=name,
            size_mb=float(card.get("size_mb") or 0),
            description=str(card.get("label") or card.get("description") or ""),
            capabilities=list(card.get("capabilities") or []),
            gated=bool(card.get("gated", False)),
            source=str(card.get("source") or ""),
            matrix=matrix,
            runtime=str(card.get("runtime") or ""),
            runtime_contract=str(card.get("runtime_contract") or ""),
            license=license_id,
            non_commercial=_is_non_commercial(license_id, card.get("non_commercial")),
            context_tokens=int(card.get("context_tokens") or 0),
            output_tokens=int(card.get("output_tokens") or 0),
            io_mime=dict(card.get("io_mime") or {}),
            status=str(card.get("status") or "active"),
            config_only=bool(card.get("config_only", False)),
        )

    @staticmethod
    def _apply_disk_state(
        model: LocalModel,
        cache_root: Path,
        layouts: Any,
    ) -> None:
        """Fill ``downloaded`` and the ``truncated`` integrity flag from disk (LMMV §2.3).

        A finished, non-``config_only`` model whose on-disk bytes fall below
        :data:`_TRUNCATION_FLOOR` of its declared footprint — and which has no unfinished
        fetch to explain the shortfall — is weights-missing, so it carries
        ``integrity="truncated"`` (the FE then offers Repair). ``config_only`` repos
        (pyannote's pipeline layout) have no local weights, so they are never flagged.

        The shortfall is excused by a partial artifact IN THE SAME DIRECTORY the bytes were
        counted from (:func:`~gideon.integrations.local_models.layouts.has_partial`), not by an
        injected set of in-flight model names. That injected set was itself never filled by
        anything but a test, so the moment the disk probe above started running for real it
        would have been an unarmed guard: measured on a two-shard HF fetch (one blob renamed,
        one still ``.incomplete``), the row came back ``integrity="truncated"`` mid-download —
        a danger chip and a Repair button offered on a healthy download in progress.
        """
        model.downloaded = layouts.is_downloaded(cache_root, model.name)
        if not model.downloaded or model.config_only:
            return
        expected = model.size_mb * 1_000_000
        if expected <= 0:
            return
        if (
            layouts.on_disk_bytes(cache_root, model.name)
            >= expected * _TRUNCATION_FLOOR
        ):
            return
        if layouts.has_partial(cache_root, model.name):
            return
        model.integrity = "truncated"
