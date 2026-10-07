"""Owner-managed model prices over active bindings and recent usage."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web

from gideon.core.config.loader import ConfigPreserveError
from gideon.core.config.transactions import ConfigWriteError
from gideon.core.http_request import read_json_body
from gideon.engine.routing.rates import (
    RatesUnreadable,
    clear_rate,
    rate_entry,
    rates_view,
    set_rate,
)
from gideon.http_errors import json_error

logger = logging.getLogger(__name__)
USED_WINDOW_DAYS = 30


def _home():
    from gideon.core.config.loader import resolve_config_dir

    return resolve_config_dir()


def _owner_only(request):
    return (
        json_error("forbidden", message="Model prices are owner-only.", status=403)
        if request.get("app")
        else None
    )


def _recent_models(home: Path):
    from gideon.operations.usage_ledger import UsageJournal

    cutoff = datetime.now(timezone.utc) - timedelta(days=USED_WINDOW_DAYS)
    result = []
    rows = UsageJournal(home / "usage" / "turns.jsonl").rows()
    # The attempt journal also names models used outside attended chat.
    audit = home / "model_calls.jsonl"
    if audit.is_file():
        try:
            for line in audit.read_text(encoding="utf-8").splitlines()[-2000:]:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except ValueError:
                    continue
        except OSError:
            logger.debug("could not read recent model attempts", exc_info=True)
    for row in rows:
        stamp = row.get("ts")
        try:
            at = (
                datetime.fromtimestamp(stamp, timezone.utc)
                if isinstance(stamp, (int, float))
                else datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            )
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, OverflowError, OSError):
            continue
        if at >= cutoff:
            provider, model = str(row.get("provider") or ""), str(
                row.get("model") or ""
            )
            if provider and model:
                result.append((provider, model))
    return result


def _view():
    from gideon.extensions.providers.use_cases import load_active_models, split_ref

    home = _home()
    bound = []
    if home.is_dir():
        for chain in load_active_models().values():
            for ref in chain if isinstance(chain, list) else ():
                pair = split_ref(str(ref))
                if pair:
                    bound.append(pair)
    return web.json_response(rates_view([*bound, *_recent_models(home)], home=home))


def _audit(request, operation, key):
    try:
        from gideon.security.sel import sel

        sel().log_api_access(
            caller=request.get("user", "dashboard"),
            operation=operation,
            outcome="success",
            source="models",
            resources=key,
        )
    except Exception:
        logger.warning("model price change audit failed", exc_info=True)


def _unsaved(error):
    if isinstance(error, (RatesUnreadable, ConfigPreserveError)):
        return json_error(
            "model_rates_unreadable",
            message="Settings could not be read; no model price was changed. Repair the settings file before saving.",
            status=409,
        )
    logger.warning("model price could not be saved", exc_info=True)
    return json_error(
        "model_rate_unsaved", message="The model price could not be saved.", status=500
    )


async def api_model_rates(request):
    denied = _owner_only(request)
    return denied if denied is not None else _view()


async def api_model_rate_put(request):
    denied = _owner_only(request)
    if denied is not None:
        return denied
    try:
        key, row = rate_entry(await read_json_body(request))
    except (ValueError, TypeError):
        return json_error(
            "bad_request",
            message="Give a model and finite, non-negative prices in its billed unit.",
            status=400,
        )
    try:
        set_rate(key, row)
    except (RatesUnreadable, ConfigPreserveError, ConfigWriteError, OSError) as error:
        return _unsaved(error)
    _audit(request, "model_rates.set", key)
    return _view()


async def api_model_rate_delete(request):
    denied = _owner_only(request)
    if denied is not None:
        return denied
    key = str(request.query.get("key") or "").strip()
    if not key:
        return json_error(
            "bad_request", message="Choose the model price to reset.", status=400
        )
    from gideon.engine.routing.rates import _read_overrides

    try:
        if key not in _read_overrides(_home() / "config.json"):
            return json_error(
                "not_found", message="No price is set for this model.", status=404
            )
        clear_rate(key)
    except (RatesUnreadable, ConfigPreserveError, ConfigWriteError, OSError) as error:
        return _unsaved(error)
    _audit(request, "model_rates.clear", key)
    return _view()


def register_model_rates_routes(app):
    app.router.add_get("/api/models/rates", api_model_rates)
    app.router.add_put("/api/models/rates", api_model_rate_put)
    app.router.add_delete("/api/models/rates", api_model_rate_delete)
