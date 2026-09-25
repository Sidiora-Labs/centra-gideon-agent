"""Human-supplied observations and explicit provider evaluations."""
from pathlib import Path
from aiohttp import web
from gideon.core.config import config_dir
from gideon.workspace.capabilities.identity.fidelity import FidelityStore
from gideon.workspace.capabilities.identity.fidelity_generation import run_evaluation
from gideon.workspace.capabilities.identity.store import ConflictError

KEY = web.AppKey("identity_fidelity", FidelityStore)
PREFIX = "/api/capabilities/identity/fidelity"


async def handle(request):
    store = request.app[KEY]
    kind = request.match_info["kind"]
    identifier = request.match_info.get("id")
    try:
        if request.method == "GET":
            if kind == "cases":
                result = store.get_case(identifier) if identifier else store.list_cases()
            else:
                result = store.get_run(identifier) if identifier else store.list_runs()
        else:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            if kind == "cases":
                result = store.save_case(**body)
            elif kind == "observations":
                result = store.record_observation(**body)
            else:
                if set(body) != {"case_id", "request_id"}:
                    raise ValueError("Expected case_id and request_id")
                result = await run_evaluation(store, **body)
        return web.json_response(result)
    except ConflictError as error:
        return web.json_response({"error": str(error)}, status=409)
    except KeyError:
        return web.json_response({"error": "Fidelity record not found"}, status=404)
    except (TypeError, ValueError) as error:
        return web.json_response({"error": str(error)}, status=400)


def register(app: web.Application, *, store_path: Path | None = None):
    app[KEY] = FidelityStore(store_path or config_dir() / "capabilities/identity/fidelity.sqlite3")
    app.router.add_get(PREFIX + "/{kind:cases|runs}", handle)
    app.router.add_get(PREFIX + "/{kind:cases|runs}/{id}", handle)
    app.router.add_post(PREFIX + "/{kind:cases|observations|run}", handle)
