"""Human identity HTTP operations using the application's authenticated boundary."""

from pathlib import Path

from aiohttp import web

from gideon.core.config import config_dir
from gideon.workspace.capabilities.identity.store import ConflictError
from gideon.workspace.capabilities.identity.twin import TwinStore
from gideon.workspace.capabilities.identity.twin_enrichment import propose_enrichment

STORE_KEY = web.AppKey("human_twin", TwinStore)
PREFIX = "/api/capabilities/identity/twin"


async def handle(request):
    store = request.app[STORE_KEY]
    operation = request.match_info.get("operation")
    try:
        if request.method == "GET":
            result = (
                store.compose(budget=int(request.query.get("budget", "1000")))
                if operation == "context"
                else store.snapshot()
            )
        elif request.method == "DELETE":
            result = store.delete_document(
                request.match_info["id"],
                int(request.query.get("expected_revision", "")),
            )
        else:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            if operation == "documents":
                result = store.save_document(**body)
            elif operation == "enrich":
                if set(body) != {"document_id"}:
                    raise ValueError("Expected document_id only")
                doc = next(
                    (
                        d
                        for d in store.snapshot()["documents"]
                        if d["id"] == body["document_id"]
                    ),
                    None,
                )
                if doc is None:
                    raise KeyError("Document not found")
                if doc["private"] or not doc["enabled"]:
                    raise ValueError("Enable a non-private source before enrichment")
                try:
                    proposal = await propose_enrichment(doc["text"])
                except Exception:
                    return web.json_response(
                        {
                            "status": "unavailable",
                            "error": "Configured enrichment provider is unavailable; no proposal was saved",
                        },
                        status=503,
                    )
                result = {
                    "status": "proposed",
                    "document_id": doc["id"],
                    "text": proposal,
                }
            else:
                result = store.configure(**body)
        return web.json_response(result)
    except ConflictError as error:
        return web.json_response({"error": str(error)}, status=409)
    except KeyError:
        return web.json_response({"error": "Identity document not found"}, status=404)
    except (TypeError, ValueError) as error:
        return web.json_response({"error": str(error)}, status=400)


def register(app: web.Application, *, store_path: Path | None = None):
    app[STORE_KEY] = TwinStore(
        store_path or config_dir() / "capabilities/identity/twin.sqlite3"
    )
    app.router.add_get(PREFIX, handle)
    app.router.add_put(PREFIX, handle)
    app.router.add_get(PREFIX + "/{operation:context}", handle)
    app.router.add_post(PREFIX + "/{operation:documents|enrich}", handle)
    app.router.add_delete(PREFIX + "/documents/{id}", handle)
