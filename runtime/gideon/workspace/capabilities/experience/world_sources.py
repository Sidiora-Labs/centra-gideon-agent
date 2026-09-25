import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from gideon.cognition.memory_service import MemoryService
from gideon.integrations.action_providers.services import get_action_services
from gideon.security.security import redact_credentials


def record(kind, key, title, status, url):
    return {
        "kind": kind,
        "id": str(key),
        "title": (redact_credentials(str(title))[0].strip() or str(key))[:200],
        "status": str(status),
        "url": url,
    }


def extended_sources(home):
    home = Path(home).resolve()
    sources, unavailable = [], []
    state = getattr(get_action_services(), "state", None)
    memory = getattr(
        getattr(state, "context_builder", None), "memory", None
    ) or getattr(state, "_standalone_memory", None)
    archive = getattr(memory, "vector_store", None)
    archive_path = getattr(archive, "_db_path", None)
    if (
        archive is None
        or archive_path is None
        or not Path(archive_path).resolve().is_relative_to(home)
    ):
        unavailable.append("memory")
    else:
        try:
            for row in MemoryService.over_vector_store(archive).episodic_list(limit=10):
                title = (str(row.get("text", "")).splitlines() or [row["id"]])[0]
                sources.append(
                    record(
                        "memory",
                        row["id"],
                        title,
                        row.get("created_at", ""),
                        "#/capabilities/knowledge?memory="
                        + quote(str(row["id"]), safe=""),
                    )
                )
        except Exception:
            unavailable.append("memory")
    path = home / "capabilities/workspace/processes.sqlite3"
    if not path.is_file():
        unavailable.append("operations")
    else:
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                for (payload,) in db.execute(
                    "SELECT payload FROM processes ORDER BY rowid DESC LIMIT 10"
                ):
                    row = json.loads(payload)
                    sources.append(
                        record(
                            "operations",
                            row["id"],
                            row["project_id"],
                            "recorded:" + row["status"],
                            "#/capabilities/workspace?process="
                            + quote(row["id"], safe=""),
                        )
                    )
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            unavailable.append("operations")
    try:
        from gideon.workspace.capabilities.platform.peers import PeerStore

        peer_root = home / "capabilities/platform"
        if (
            not (peer_root / "peers.sqlite3").is_file()
            or not (peer_root / "identity.key").is_file()
        ):
            raise ValueError("Peer identity is not configured")
        snapshot = PeerStore(home).snapshot()
        for row in snapshot["peers"][:10]:
            probe = row.get("last_probe")
            status = "disabled" if not row["enabled"] else "configured"
            if probe:
                status += "; probe succeeded at " + str(probe)
            sources.append(
                record(
                    "peers", row["id"], row["label"], status, "#/capabilities/platform"
                )
            )
    except (ImportError, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        unavailable.append("peers")
    return {"sources": sources, "unavailable": unavailable}
