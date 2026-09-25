import json
from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import config_dir

from .eyes import EyePrescriptionStore
from .store import MeasurementError

EYE_STORE = web.AppKey("wellbeing_eye_prescriptions", EyePrescriptionStore)


async def handle(request: web.Request) -> web.Response:
    store = request.app[EYE_STORE]
    try:
        if request.query.keys() - {"from", "to", "limit", "offset"}:
            raise ValueError("Unknown query field")
        identity = request.match_info.get("id")
        if request.method in {"POST", "PUT"}:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Expected JSON object")
            result = (
                store.create(payload)
                if request.method == "POST"
                else store.correct(identity, payload)
            )
            return web.json_response(
                result, status=201 if request.method == "POST" else 200
            )
        if request.path.endswith("/export"):
            return web.json_response(
                store.export(), headers={"Cache-Control": "no-store"}
            )
        if request.path.endswith("/history"):
            return web.json_response({"history": store.history(identity)})
        if identity:
            return web.json_response(store.get(identity))
        return web.json_response(
            {
                "prescriptions": store.list(
                    from_date=request.query.get("from"),
                    to_date=request.query.get("to"),
                    limit=int(request.query.get("limit", "100")),
                    offset=int(request.query.get("offset", "0")),
                )
            }
        )
    except MeasurementError as exc:
        return web.json_response(
            {"error": str(exc), "code": exc.code}, status=exc.status
        )
    except (ValueError, TypeError, OverflowError, json.JSONDecodeError):
        return web.json_response(
            {"error": "Invalid eye prescription request", "code": "invalid_request"},
            status=400,
        )


def register(app: web.Application, home: Path | None = None) -> None:
    app[EYE_STORE] = EyePrescriptionStore(home if home is not None else config_dir())
    base = "/api/capabilities/wellbeing/eyes"
    app.router.add_get(base, handle)
    app.router.add_post(base, handle)
    app.router.add_get(base + "/export", handle)
    app.router.add_get(base + "/{id}/history", handle)
    app.router.add_get(base + "/{id}", handle)
    app.router.add_put(base + "/{id}", handle)
