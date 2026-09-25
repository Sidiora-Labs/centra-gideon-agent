"""Autobiography HTTP operations; authentication is supplied by the dashboard."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from gideon.core.config import config_dir
from gideon.workspace.capabilities.identity.store import ConflictError, StoryStore

STORE_KEY = web.AppKey("identity_story_store", StoryStore)
PREFIX = "/api/capabilities/identity"


async def handle(request: web.Request) -> web.Response:
    store = request.app[STORE_KEY]
    story_id = request.match_info.get("story_id")
    operation = request.match_info.get("operation")
    try:
        if request.method == "GET":
            if operation == "export":
                result = store.export()
            elif operation == "chain":
                result = store.chain(story_id)
            elif operation == "history":
                result = store.history(story_id)
            else:
                result = store.get(story_id) if story_id else store.list()
        else:
            body = (
                {"expected_revision": int(request.query.get("expected_revision", ""))}
                if request.method == "DELETE"
                else await request.json()
            )
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            fields = {"prompt", "theme", "text", "parent_id"}
            allowed = (
                {"expected_revision"}
                if request.method == "DELETE"
                else fields
                | {"request_id" if request.method == "POST" else "expected_revision"}
            )
            if body.keys() - allowed:
                raise ValueError("Unknown fields")
            if request.method == "POST":
                result = store.create(**body)
            elif request.method == "PUT":
                result = store.update(story_id, **body)
            else:
                store.delete(story_id, **body)
                result = {"deleted": story_id}
        return web.json_response(result)
    except ConflictError as error:
        return web.json_response({"error": str(error)}, status=409)
    except KeyError:
        return web.json_response({"error": "Story not found"}, status=404)
    except (ValueError, TypeError) as error:
        return web.json_response({"error": str(error)}, status=400)


def register(app: web.Application, *, store_path: Path | None = None) -> None:
    app[STORE_KEY] = StoryStore(
        store_path or config_dir() / "capabilities/identity/stories.sqlite3"
    )
    app.router.add_get(PREFIX + "/{operation:export}", handle)
    app.router.add_get(PREFIX + "/stories", handle)
    app.router.add_post(PREFIX + "/stories", handle)
    app.router.add_get(PREFIX + "/stories/{story_id}/{operation:chain|history}", handle)
    app.router.add_get(PREFIX + "/stories/{story_id}", handle)
    app.router.add_put(PREFIX + "/stories/{story_id}", handle)
    app.router.add_delete(PREFIX + "/stories/{story_id}", handle)
