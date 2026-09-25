"""Explicit Meshy image-to-3D jobs with canonical inputs and returned GLB assets."""

import asyncio
import base64
import io
import json
import re
import sqlite3
import struct
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from PIL import Image

from gideon.sdk.credentials import CredentialStore

from .store import DomainError, integer, text

ENDPOINT = "https://api.meshy.ai/openapi/v1/image-to-3d"
MODELS = ("meshy-6", "meshy-6-lite", "meshy-7.1")
MAX_BYTES = 16 * 1024 * 1024


def _validate_glb(raw):
    if len(raw) < 20 or len(raw) > MAX_BYTES or raw[:4] != b"glTF":
        raise DomainError("Expected bounded binary glTF", 422, "invalid_model")
    version, size = struct.unpack_from("<II", raw, 4)
    if version != 2 or size != len(raw):
        raise DomainError("Invalid GLB header", 422, "invalid_model")
    offset, document = 12, None
    while offset < size:
        if offset + 8 > size:
            raise DomainError("Truncated GLB chunk", 422, "invalid_model")
        length, kind = struct.unpack_from("<II", raw, offset)
        offset += 8
        if length % 4 or offset + length > size:
            raise DomainError("Invalid GLB chunk length", 422, "invalid_model")
        if kind == 0x4E4F534A:
            if document is not None:
                raise DomainError("Duplicate GLB JSON chunk", 422, "invalid_model")
            try:
                document = json.loads(raw[offset : offset + length])
            except (ValueError, UnicodeError) as exc:
                raise DomainError("Invalid GLB JSON", 422, "invalid_model") from exc
        offset += length
    if (
        not isinstance(document, dict)
        or document.get("asset", {}).get("version") != "2.0"
        or not document.get("meshes")
    ):
        raise DomainError("GLB has no mesh geometry", 422, "invalid_model")
    for entry in document.get("buffers", []) + document.get("images", []):
        if entry.get("uri") and not entry["uri"].startswith("data:"):
            raise DomainError(
                "Model must embed all buffers and textures",
                422,
                "external_model_resource",
            )
    return {
        "meshes": len(document["meshes"]),
        "animations": [
            row.get("name", str(i))
            for i, row in enumerate(document.get("animations", []))
        ],
    }


def validate_glb(raw):
    try:
        return _validate_glb(raw)
    except DomainError:
        raise
    except (
        AttributeError,
        TypeError,
        KeyError,
        struct.error,
        UnicodeError,
        ValueError,
    ) as exc:
        raise DomainError("Malformed GLB structure", 422, "invalid_model") from exc


async def response_json(response):
    chunks, size = [], 0
    async for chunk in response.content.iter_chunked(16384):
        size += len(chunk)
        if size > 262144:
            raise DomainError("Provider JSON exceeds limit", 502, "provider_error")
        chunks.append(chunk)
    return json.loads(b"".join(chunks))


_REFRESH_LOCKS = {}


def asset_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "assets.meshy.ai"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise DomainError("Unexpected provider asset host", 502, "provider_asset_url")
    return value


class Image3DStore:
    def __init__(self, home, artifacts, credential_resolver=None):
        self.home, self.artifacts = Path(home), artifacts
        self.root = self.home / "capabilities" / "music"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "image3d.sqlite3"
        self.credentials = (
            CredentialStore(self.home) if credential_resolver is None else None
        )
        self.resolve = credential_resolver or self._credential
        with self._db() as db:
            db.execute("PRAGMA user_version=1")
            db.execute(
                "CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY,payload TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,fingerprint TEXT,payload TEXT)"
            )

    def _credential(self, name):
        self.credentials.reload()
        try:
            result = self.credentials.resolve(name)
            return result.secret if result else None
        except KeyError:
            return None

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def config(self):
        with self._db() as db:
            row = db.execute("SELECT payload FROM config WHERE id=1").fetchone()
            return (
                json.loads(row[0])
                if row
                else {
                    "enabled": False,
                    "credential_name": "",
                    "model": MODELS[0],
                    "revision": 0,
                }
            )

    def configure(self, data):
        if (
            not isinstance(data, dict)
            or set(data) != {"enabled", "credential_name", "model", "revision"}
            or type(data["enabled"]) is not bool
            or data["model"] not in MODELS
        ):
            raise DomainError("Invalid image-to-3D configuration")
        text(data["credential_name"], "credential_name", 100)
        integer(data["revision"], "revision", 0, 1000000)
        with self._db() as db:
            row = db.execute("SELECT payload FROM config WHERE id=1").fetchone()
            revision = json.loads(row[0])["revision"] if row else 0
            if revision != data["revision"]:
                raise DomainError(
                    "3D engine configuration changed", 409, "revision_conflict"
                )
            value = {**data, "revision": revision + 1}
            db.execute(
                "INSERT OR REPLACE INTO config VALUES (1,?)", (json.dumps(value),)
            )
            return value

    def readiness(self):
        config = self.config()
        available = bool(
            config["credential_name"] and self.resolve(config["credential_name"])
        )
        return {
            "config": config,
            "credential_available": available,
            "ready_to_submit": config["enabled"] and available,
            "provider": "meshy",
            "remote_status": "unverified",
        }

    def list(self):
        with self._db() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 100"
                )
            ]

    def get(self, item_id):
        with self._db() as db:
            row = db.execute(
                "SELECT payload FROM jobs WHERE id=?", (text(item_id, "id", 200, True),)
            ).fetchone()
            if not row:
                raise DomainError("3D generation job not found", 404, "not_found")
            return json.loads(row[0])

    def _save(self, job):
        with self._db() as db:
            db.execute(
                "UPDATE jobs SET payload=? WHERE id=?", (json.dumps(job), job["id"])
            )

    def request(self, data):
        if not isinstance(data, dict) or set(data) != {
            "request_id",
            "title",
            "image_ref",
            "target_polycount",
            "should_texture",
            "license",
        }:
            raise DomainError(
                "Image job requires request_id,title,image_ref,target_polycount,should_texture,license"
            )
        for key in ("request_id", "title", "license"):
            text(data[key], key, 500 if key == "license" else 200, True)
        integer(data["target_polycount"], "target_polycount", 100, 300000)
        if type(data["should_texture"]) is not bool:
            raise DomainError("Texture option must be boolean")
        ref = data["image_ref"]
        if not isinstance(ref, dict) or set(ref) != {"slug", "version"}:
            raise DomainError("Canonical source image reference required")
        text(ref["slug"], "slug", 200, True)
        integer(ref["version"], "version", 1, 1000000)
        artifact = self.artifacts.get(ref["slug"], version=ref["version"])
        raw = (
            self.artifacts.raw_bytes(ref["slug"], version=ref["version"])
            if artifact
            else None
        )
        if (
            not artifact
            or artifact.kind != "image"
            or not raw
            or raw[1] not in ("image/png", "image/jpeg")
            or len(raw[0]) > 8 * 1024 * 1024
        ):
            raise DomainError(
                "PNG or JPEG source image unavailable", 422, "invalid_image"
            )
        try:
            with Image.open(io.BytesIO(raw[0])) as image:
                if image.width * image.height > 16000000:
                    raise ValueError("Image exceeds limit")
                image.verify()
        except Exception as exc:
            raise DomainError("Invalid source image", 422, "invalid_image") from exc
        return data, raw

    def payload(self, request, raw, model):
        return {
            "image_url": "data:"
            + raw[1]
            + ";base64,"
            + base64.b64encode(raw[0]).decode(),
            "ai_model": model,
            "model_type": "standard",
            "should_remesh": True,
            "target_polycount": request["target_polycount"],
            "should_texture": request["should_texture"],
            "target_formats": ["glb"],
        }

    async def submit(self, data):
        request, raw = self.request(data)
        fingerprint = json.dumps(request, sort_keys=True)
        config = self.config()
        with self._db() as db:
            prior = db.execute(
                "SELECT fingerprint,payload FROM jobs WHERE id=?",
                (request["request_id"],),
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise DomainError(
                        "Request ID already has different input",
                        409,
                        "request_conflict",
                    )
                return json.loads(prior[1])
            secret = (
                self.resolve(config["credential_name"]) if config["enabled"] else None
            )
            if not secret:
                raise DomainError(
                    "Configure an available named image-to-3D credential",
                    503,
                    "provider_unavailable",
                )
            job = {
                "id": request["request_id"],
                "status": "submitting",
                "request": request,
                "model": config["model"],
                "credential_name": config["credential_name"],
                "provider_task_id": None,
                "progress": 0,
                "artifact_ref": None,
                "error": None,
            }
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?)",
                (job["id"], fingerprint, json.dumps(job)),
            )
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120)
            ) as session:
                async with session.post(
                    ENDPOINT,
                    json=self.payload(request, raw, job["model"]),
                    headers={"Authorization": "Bearer " + secret},
                    allow_redirects=False,
                ) as response:
                    if response.status != 202 and response.status != 200:
                        raise DomainError(
                            "3D provider returned HTTP " + str(response.status),
                            502,
                            "provider_error",
                        )
                    result = (await response_json(response))["result"]
                    if not isinstance(result, str) or not re.fullmatch(
                        r"[A-Za-z0-9-]{1,100}", result
                    ):
                        raise ValueError("Invalid provider task ID")
                    job = self.get(job["id"])
                    job.update(
                        provider_task_id=result,
                        status="stopped" if job["status"] == "stopped" else "pending",
                    )
                    self._save(job)
        except Exception:
            job = self.get(job["id"])
            if job["status"] == "stopped":
                return job
            job.update(
                status="interrupted",
                error="Provider submission not confirmed; do not resubmit under a new ID until account status is checked",
            )
            self._save(job)
        return job

    async def refresh(self, item_id):
        key = (str(self.path.resolve()), item_id)
        async with _REFRESH_LOCKS.setdefault(key, asyncio.Lock()):
            return await self._refresh(item_id)

    async def _refresh(self, item_id):
        job = self.get(item_id)
        if job["status"] in ("completed", "failed", "stopped"):
            return job
        if not job["provider_task_id"]:
            raise DomainError(
                "Submission outcome unknown; inspect provider account",
                409,
                "submission_unknown",
            )
        secret = self.resolve(job["credential_name"])
        if not secret:
            raise DomainError(
                "Named credential unavailable", 503, "provider_unavailable"
            )
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120)
        ) as session:
            async with session.get(
                ENDPOINT + "/" + job["provider_task_id"],
                headers={"Authorization": "Bearer " + secret},
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise DomainError(
                        "Cannot retrieve 3D task status", 502, "provider_error"
                    )
                result = await response_json(response)
            if self.get(item_id)["status"] == "stopped":
                return self.get(item_id)
            status = result.get("status")
            if status in ("FAILED", "CANCELED"):
                job.update(
                    status="failed", error="Provider did not complete reconstruction"
                )
            elif status in ("PENDING", "IN_PROGRESS"):
                job.update(
                    status="pending" if status == "PENDING" else "running",
                    progress=integer(result.get("progress", 0), "progress", 0, 100),
                )
            elif status == "SUCCEEDED":
                url = asset_url(result.get("model_urls", {}).get("glb", ""))
                async with session.get(url, allow_redirects=False) as response:
                    if response.status != 200:
                        raise DomainError(
                            "Generated GLB download unavailable", 502, "download_failed"
                        )
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise DomainError(
                                "Generated GLB exceeds artifact size limit",
                                422,
                                "model_too_large",
                            )
                        chunks.append(chunk)
                if self.get(item_id)["status"] == "stopped":
                    return self.get(item_id)
                raw = b"".join(chunks)
                metadata = validate_glb(raw)
                artifact = self.artifacts.create_binary(
                    name=job["request"]["title"],
                    data=raw,
                    kind="model",
                    mime="model/gltf-binary",
                    source="manual",
                    event_metadata={
                        "provider": "meshy",
                        "model": job["model"],
                        "task_id": job["provider_task_id"],
                    },
                )
                job.update(
                    status="completed",
                    progress=100,
                    artifact_ref={"slug": artifact.slug, "version": artifact.version},
                    geometry=metadata,
                    error=None,
                )
            else:
                raise DomainError(
                    "Unknown image-to-3D provider status", 502, "provider_error"
                )
        self._save(job)
        return job

    def stop(self, item_id):
        job = self.get(item_id)
        if job["status"] not in ("completed", "failed", "stopped"):
            job.update(
                status="stopped",
                error="Local tracking stopped; provider completion and billing are unchanged or unknown",
            )
            self._save(job)
        return job
