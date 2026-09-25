"""Fixed Spotify Web API reads using an existing named OAuth connection."""

import asyncio
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import aiohttp

from gideon.sdk.credentials import CredentialStore

from .image3d import response_json
from .listening import digest
from .store import DomainError, integer, text

BASE = "https://api.spotify.com/v1"
_LOCKS = {}


class SpotifyBridge:
    def __init__(self, home, listening, credential_resolver=None):
        self.home, self.listening = Path(home), listening
        self.path = listening.root / "spotify.sqlite3"
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
                "CREATE TABLE IF NOT EXISTS syncs(id TEXT PRIMARY KEY,fingerprint TEXT,payload TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS cursors(account_id TEXT PRIMARY KEY,value INTEGER)"
            )

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

    def _credential(self, name):
        self.credentials.reload()
        try:
            secret = self.credentials.resolve(name).secret
            try:
                token = json.loads(secret or "")
            except ValueError:
                return secret
            return token.get("access_token") if isinstance(token, dict) else secret
        except KeyError:
            return None

    def config(self):
        with self._db() as db:
            row = db.execute("SELECT payload FROM config WHERE id=1").fetchone()
            return (
                json.loads(row[0])
                if row
                else {
                    "enabled": False,
                    "credential_name": "",
                    "account_label": "",
                    "revision": 0,
                }
            )

    def configure(self, data):
        if (
            not isinstance(data, dict)
            or set(data) != {"enabled", "credential_name", "account_label", "revision"}
            or type(data["enabled"]) is not bool
        ):
            raise DomainError("Invalid Spotify connection configuration")
        text(data["credential_name"], "credential name", 100)
        text(data["account_label"], "account label", 200)
        integer(data["revision"], "revision", 0, 1000000)
        with self._db() as db:
            row = db.execute("SELECT payload FROM config WHERE id=1").fetchone()
            revision = json.loads(row[0])["revision"] if row else 0
            if revision != data["revision"]:
                raise DomainError(
                    "Spotify configuration changed", 409, "revision_conflict"
                )
            result = {**data, "revision": revision + 1}
            db.execute(
                "INSERT OR REPLACE INTO config VALUES (1,?)", (json.dumps(result),)
            )
            return result

    def readiness(self):
        config = self.config()
        available = bool(
            config["credential_name"] and self.resolve(config["credential_name"])
        )
        return {
            "config": config,
            "ready_to_sync": config["enabled"] and available,
            "credential_available": available,
            "remote_status": "unverified",
            "authorization_owner": "named_oauth_connection",
        }

    async def _get(self, session, path, params=None):
        async with session.get(
            BASE + path, params=params, allow_redirects=False
        ) as response:
            if response.status != 200:
                raise DomainError(
                    "Spotify returned HTTP " + str(response.status),
                    response.status if response.status in (401, 403, 429) else 502,
                    "spotify_error",
                )
            return await response_json(response)

    async def _pages(self, session, path, params=None):
        items = []
        for _ in range(20):
            page = await self._get(session, path, params)
            if not isinstance(page.get("items"), list):
                raise DomainError("Invalid Spotify page", 502, "spotify_error")
            items.extend(page["items"])
            next_url = page.get("next")
            if not next_url:
                return items
            parsed = urlsplit(next_url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "api.spotify.com"
                or parsed.path != "/v1" + path
            ):
                raise DomainError(
                    "Unexpected Spotify pagination URL", 502, "spotify_error"
                )
            params = dict(parse_qsl(parsed.query))
        raise DomainError(
            "Spotify pagination exceeds bounded sync; cursor not advanced",
            422,
            "sync_limit",
        )

    async def sync(self, data):
        if not isinstance(data, dict) or set(data) != {"request_id", "playlist_ids"}:
            raise DomainError("Sync requires request_id,playlist_ids")
        text(data["request_id"], "request_id", 200, True)
        ids = data["playlist_ids"]
        if (
            not isinstance(ids, list)
            or len(ids) > 10
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[A-Za-z0-9]{1,64}", value)
                for value in ids
            )
            or len(set(ids)) != len(ids)
        ):
            raise DomainError("At most ten unique Spotify playlist IDs")
        key = str(self.path.resolve())
        async with _LOCKS.setdefault(key, asyncio.Lock()):
            return await self._sync(data)

    async def _sync(self, data):
        fingerprint = digest(data)
        with self._db() as db:
            row = db.execute(
                "SELECT fingerprint,payload FROM syncs WHERE id=?",
                (data["request_id"],),
            ).fetchone()
            if row and row[0] != fingerprint:
                raise DomainError(
                    "Spotify sync request ID conflict", 409, "request_conflict"
                )
            job = json.loads(row[1]) if row else None
            if job and job.get("receipt"):
                return {**job["receipt"], "replayed": True}
        if job is None:
            config = self.config()
            token = None
            if config["enabled"]:
                if self.credentials is not None:
                    from .spotify_oauth import SpotifyOAuth

                    token = await SpotifyOAuth(self).access(config["credential_name"])
                else:
                    token = self.resolve(config["credential_name"])
            if not token:
                raise DomainError(
                    "Available named OAuth connection required",
                    503,
                    "spotify_unavailable",
                )
            async with aiohttp.ClientSession(
                headers={"Authorization": "Bearer " + token},
                timeout=aiohttp.ClientTimeout(total=90),
            ) as session:
                account = await self._get(session, "/me")
                account_id = text(account.get("id"), "Spotify account ID", 200, True)
                with self._db() as db:
                    row = db.execute(
                        "SELECT value FROM cursors WHERE account_id=?", (account_id,)
                    ).fetchone()
                    cursor = row[0] if row else None
                recent = await self._pages(
                    session,
                    "/me/player/recently-played",
                    {"limit": 50, **({"after": cursor} if cursor else {})},
                )
                playlists = []
                for playlist_id in data["playlist_ids"]:
                    playlist = await self._get(session, "/playlists/" + playlist_id)
                    items = await self._pages(
                        session, "/playlists/" + playlist_id + "/items", {"limit": 50}
                    )
                    playlists.append(
                        {
                            "id": playlist_id,
                            "name": playlist["name"],
                            "snapshot_id": playlist.get("snapshot_id", ""),
                            "items": items,
                        }
                    )
            snapshot = {
                "recent": recent,
                "playlists": playlists,
                "account_id": account_id,
            }
            content = json.dumps(snapshot)
            if len(content.encode()) > 1024 * 1024:
                raise DomainError(
                    "Sync exceeds canonical JSON size limit; cursor not advanced",
                    422,
                    "sync_limit",
                )
            artifact = self.listening.artifacts.create(
                name="Spotify listening snapshot",
                content=content,
                kind="json",
                source="import",
            )
            observed = [
                int(
                    datetime.fromisoformat(
                        row["played_at"].replace("Z", "+00:00")
                    ).timestamp()
                    * 1000
                )
                for row in recent
            ]
            next_cursor = max([cursor or 0, *observed]) or None
            job = {
                "account_id": account_id,
                "artifact_ref": {"slug": artifact.slug, "version": artifact.version},
                "cursor": next_cursor,
                "receipt": None,
            }
            with self._db() as db:
                db.execute(
                    "INSERT INTO syncs VALUES (?,?,?)",
                    (data["request_id"], fingerprint, json.dumps(job)),
                )
        imported = self.listening.import_data(
            {
                "request_id": "spotify-sync-" + digest(data["request_id"]),
                "account_label": "spotify:" + job["account_id"],
                "format": "spotify_api",
                "artifact_ref": job["artifact_ref"],
            }
        )
        receipt = {
            "request_id": data["request_id"],
            "account_id": job["account_id"],
            "import_receipt": imported,
            "cursor": job["cursor"],
            "replayed": False,
        }
        job["receipt"] = receipt
        with self._db() as db:
            if job["cursor"] is not None:
                db.execute(
                    "INSERT OR REPLACE INTO cursors VALUES (?,?)",
                    (job["account_id"], job["cursor"]),
                )
            db.execute(
                "UPDATE syncs SET payload=? WHERE id=?",
                (json.dumps(job), data["request_id"]),
            )
        return receipt
