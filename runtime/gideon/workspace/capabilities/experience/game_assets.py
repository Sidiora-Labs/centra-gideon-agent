import base64
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.extensions.apps.manager import _read_installed, app_data_dir
from gideon.workspace.artifacts.native import NativeArtifactProvider
from gideon.workspace.capabilities.music.image3d import validate_glb

from .store import Conflict, NotFound

ROLES = {
    "sprite": ("image", {"image/png", "image/jpeg", "image/webp"}),
    "artwork": ("image", {"image/png", "image/jpeg", "image/webp"}),
    "music": ("audio", {"audio/wav", "audio/mpeg", "audio/ogg"}),
    "model": ("model", {"model/gltf-binary"}),
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha(value):
    return hashlib.sha256(
        value if isinstance(value, bytes) else value.encode()
    ).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value):
        raise ValueError("Invalid game identifier")
    return value


def _text(value, label):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 100
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError(f"{label} must contain 1 to 100 visible characters")
    return value.strip()


class GameAssets:
    def __init__(self, store, artifacts=None):
        self.store = store
        self.artifacts = artifacts or NativeArtifactProvider(
            store.path.parent.parent / "artifacts"
        )
        with store.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS game_asset_projects(id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )

    def _save(self, record):
        with self.store.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO game_asset_projects VALUES(?,?)",
                (record["id"], _canonical(record)),
            )
        return record

    def get(self, key):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT body FROM game_asset_projects WHERE id=?", (_identifier(key),)
            ).fetchone()
        if not row:
            raise NotFound("game asset project not found")
        return json.loads(row[0])

    def list(self):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT body FROM game_asset_projects ORDER BY rowid DESC LIMIT 100"
            ).fetchall()
        return {
            "projects": [json.loads(row[0]) for row in rows],
            "required_roles": list(ROLES),
        }

    def _foundation(self, key):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT body FROM world_foundations WHERE id=?", (_identifier(key),)
            ).fetchone()
        if not row:
            raise NotFound("foundation not found")
        foundation = json.loads(row[0])
        if foundation["state"] not in ("promoted", "adopted") or not foundation.get(
            "fingerprint"
        ):
            raise Conflict("Game export requires a promoted or adopted foundation")
        return foundation

    def _app(self, app_id):
        _identifier(app_id)
        app = _read_installed(app_id)
        if not app or not app.enabled:
            raise Conflict("Bound managed app is not installed and enabled")
        return app

    def create(self, body):
        if not isinstance(body, dict) or set(body) != {
            "title",
            "app_id",
            "foundation_id",
        }:
            raise ValueError("Project requires title, app_id and foundation_id")
        app, foundation = self._app(body["app_id"]), self._foundation(
            body["foundation_id"]
        )
        record = {
            "id": "game_" + uuid4().hex,
            "revision": 1,
            "title": _text(body["title"], "Project title"),
            "app_id": app.name,
            "foundation_id": foundation["id"],
            "foundation_fingerprint": foundation["fingerprint"],
            "bindings": {},
            "compiled": None,
            "compile_history": [],
            "publication": None,
        }
        return self._save(record)

    def _expected(self, key, revision):
        if type(revision) is not int:
            raise ValueError("Expected revision is required")
        record = self.get(key)
        if record["revision"] != revision:
            raise Conflict("game asset project revision changed")
        return record

    def _asset(self, role, ref):
        if (
            role not in ROLES
            or not isinstance(ref, dict)
            or set(ref) != {"slug", "version"}
            or type(ref["version"]) is not int
        ):
            raise ValueError(
                "Binding requires a supported role and exact artifact version"
            )
        artifact = self.artifacts.get(ref["slug"], version=ref["version"])
        raw = (
            self.artifacts.raw_bytes(ref["slug"], version=ref["version"])
            if artifact
            else None
        )
        expected_kind, mimes = ROLES[role]
        if (
            not artifact
            or artifact.kind != expected_kind
            or not raw
            or raw[1] not in mimes
        ):
            raise Conflict(f"{role} artifact version or bytes are unavailable")
        data, mime = raw
        if not data or len(data) > 512 * 1024:
            raise Conflict(f"{role} artifact bytes exceed the runnable export limit")
        if role in ("sprite", "artwork") and not (
            data.startswith(b"\x89PNG\r\n\x1a\n")
            or data[:2] == b"\xff\xd8"
            or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        ):
            raise Conflict(f"{role} artifact bytes are corrupt")
        if role == "music" and not (
            data.startswith(b"RIFF")
            or data.startswith(b"ID3")
            or data.startswith(b"OggS")
        ):
            raise Conflict("music artifact bytes are corrupt")
        if role == "model":
            validate_glb(data)
        return artifact, data, mime

    def bind(self, key, body):
        if not isinstance(body, dict) or set(body) != {
            "revision",
            "role",
            "artifact_ref",
            "label",
        }:
            raise ValueError("Binding requires revision, role, artifact_ref and label")
        record = self._expected(key, body["revision"])
        artifact, data, mime = self._asset(body["role"], body["artifact_ref"])
        record["bindings"][body["role"]] = {
            "label": _text(body["label"], "Asset label"),
            "artifact_ref": {"slug": artifact.slug, "version": artifact.version},
            "kind": artifact.kind,
            "mime": mime,
            "sha256": _sha(data),
            "size": len(data),
        }
        record.update(revision=record["revision"] + 1, compiled=None, publication=None)
        return self._save(record)

    def compile(self, key, revision):
        record = self._expected(key, revision)
        self._app(record["app_id"])
        foundation = self._foundation(record["foundation_id"])
        if foundation["fingerprint"] != record["foundation_fingerprint"]:
            raise Conflict("Foundation fingerprint changed")
        if set(record["bindings"]) != set(ROLES):
            raise Conflict("Sprite, artwork, music and model bindings are required")
        resolved = {}
        for role, binding in record["bindings"].items():
            _artifact, data, mime = self._asset(role, binding["artifact_ref"])
            if _sha(data) != binding["sha256"]:
                raise Conflict(f"{role} artifact integrity changed")
            resolved[role] = (data, mime)
        inputs = {
            "app_id": record["app_id"],
            "foundation": record["foundation_fingerprint"],
            "bindings": record["bindings"],
        }
        input_sha = _sha(_canonical(inputs))
        current = record["compiled"]
        if current and current["input_sha256"] == input_sha:
            export = self.artifacts.get(
                current["export_ref"]["slug"], version=current["export_ref"]["version"]
            )
            if (
                export
                and export.content is not None
                and _sha(export.content) == current["export_sha256"]
            ):
                return {**current, "created": False}
        version = (
            max([item["version"] for item in record["compile_history"]] or [0]) + 1
        )
        manifest = {
            "schema_version": 1,
            "kind": "gideon-game-assets",
            "project": {
                "id": record["id"],
                "title": record["title"],
                "app_id": record["app_id"],
            },
            "foundation": {
                "id": foundation["id"],
                "fingerprint": foundation["fingerprint"],
                "provenance": foundation["provenance"],
            },
            "version": version,
            "assets": record["bindings"],
        }
        urls = {
            role: f"data:{mime};base64," + base64.b64encode(data).decode()
            for role, (data, mime) in resolved.items()
        }
        document = self._document(record["title"], manifest, urls)
        export = self.artifacts.create(
            name=f"{record['title']} game export v{version}",
            content=document,
            kind="html",
            source="manual",
            project_id=record["id"],
            readonly=True,
            event_metadata={"game_project": record["id"], "input_sha256": input_sha},
        )
        pointer = {
            "version": version,
            "input_sha256": input_sha,
            "manifest_sha256": _sha(_canonical(manifest)),
            "export_ref": {"slug": export.slug, "version": export.version},
            "export_sha256": _sha(document),
            "asset_count": len(resolved),
            "created_at": _now(),
        }
        record.update(
            revision=record["revision"] + 1,
            compiled=pointer,
            compile_history=(record["compile_history"] + [pointer])[-50:],
            publication=None,
        )
        self._save(record)
        return {**pointer, "created": True}

    def publish(self, key, revision):
        record = self._expected(key, revision)
        self._app(record["app_id"])
        pointer = record["compiled"]
        if not pointer:
            raise Conflict("Compile the game before publication")
        export = self.artifacts.get(
            pointer["export_ref"]["slug"], version=pointer["export_ref"]["version"]
        )
        if (
            not export
            or export.content is None
            or _sha(export.content) != pointer["export_sha256"]
        ):
            raise Conflict("Compiled export is missing or corrupt")
        relative = Path("game-assets") / record["id"] / "index.html"
        destination = app_data_dir(record["app_id"]) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(export.content, encoding="utf-8")
        temporary.replace(destination)
        if _sha(destination.read_bytes()) != pointer["export_sha256"]:
            raise Conflict("Managed app destination verification failed")
        receipt = {
            "state": "verified",
            "app_id": record["app_id"],
            "destination": relative.as_posix(),
            "destination_sha256": pointer["export_sha256"],
            "export_ref": pointer["export_ref"],
            "published_at": _now(),
        }
        record.update(revision=record["revision"] + 1, publication=receipt)
        self._save(record)
        return receipt

    @staticmethod
    def _document(title, manifest, urls):
        payload, assets = _canonical(manifest).replace("</", "<\\/"), _canonical(
            urls
        ).replace("</", "<\\/")
        return (
            "<!doctype html><meta charset=utf-8><title>"
            + html.escape(title)
            + "</title><style>html,body{margin:0;background:#101322;color:white;font:16px sans-serif}canvas{width:100vw;height:80vh;object-fit:cover}button,a{margin:8px}</style><canvas width=960 height=540></canvas><button>Play music</button><a id=model download=game.glb>Download model</a><script>const manifest="
            + payload
            + ";const assets="
            + assets
            + ";const c=document.querySelector('canvas'),x=c.getContext('2d'),bg=new Image(),hero=new Image();bg.src=assets.artwork;hero.src=assets.sprite;let p={x:430,y:230};addEventListener('keydown',e=>{if(e.key==='ArrowLeft')p.x-=12;if(e.key==='ArrowRight')p.x+=12;if(e.key==='ArrowUp')p.y-=12;if(e.key==='ArrowDown')p.y+=12});function frame(){x.drawImage(bg,0,0,c.width,c.height);x.drawImage(hero,p.x,p.y,96,96);x.fillText(manifest.project.title,20,30);requestAnimationFrame(frame)}bg.onload=frame;const audio=new Audio(assets.music);document.querySelector('button').onclick=()=>audio.play();document.querySelector('#model').href=assets.model;globalThis.__GIDEON_GAME_MANIFEST__=manifest;</script>"
        )
