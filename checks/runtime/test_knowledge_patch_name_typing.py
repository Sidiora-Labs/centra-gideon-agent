import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.interfaces.dashboard.handlers import knowledge
from gideon.interfaces.dashboard.request_boundary import request_boundary_middleware


def _request(store, path, item_id, body):
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    request = make_mocked_request(
        "PATCH",
        path,
        headers={"Content-Type": "application/json"},
        app=app,
        match_info={"id": str(item_id)},
    )
    request._read_bytes = json.dumps(body).encode()
    return request


@pytest.mark.parametrize(
    ("handler", "path", "field", "expected", "arrange", "read"),
    [
        (
            knowledge.update_item,
            "/api/knowledge/items/item",
            "title",
            {"error": "title must be a string"},
            lambda store: store.create_typed_item(
                item_type="note", title="Original item", content="body"
            ),
            lambda store, item_id: store.get_item(item_id)["title"],
        ),
        (
            knowledge.update_collection,
            "/api/knowledge/collections/collection",
            "name",
            {"error": "name must be a string"},
            lambda store: store.create_collection(name="Original collection"),
            lambda store, item_id: store.get_collection(item_id)["name"],
        ),
        (
            knowledge.rename_tag,
            "/api/knowledge/tags/tag",
            "name",
            {"error": "name must be a string"},
            lambda store: (
                store.create_typed_item(
                    item_type="note",
                    title="Tagged",
                    content="body",
                    tags=["Original tag"],
                ),
                store.list_tags()[0]["id"],
            )[1],
            lambda store, item_id: next(
                tag["name"] for tag in store.list_tags() if tag["id"] == item_id
            ),
        ),
        (
            knowledge.update_watched_source,
            "/api/knowledge/sources/source",
            "name",
            {
                "error": {
                    "code": "bad_request",
                    "message": "The request was malformed or carried an unusable parameter.",
                }
            },
            lambda store: store.create_source(
                name="Original source", provider="test", kind="test"
            ),
            lambda store, item_id: store.get_source(item_id)["name"],
        ),
    ],
)
async def test_patch_name_fields_reject_wrong_types_without_persisting_repr(
    tmp_path, handler, path, field, expected, arrange, read
):
    """Every response is measured through the real request-boundary middleware.

    Three handlers catch the bad type themselves; `update_watched_source` lets the
    `RequestValidationError` reach the gate, which is the production path for it too.
    """
    store = KnowledgeStore(tmp_path / "knowledge.db")
    item_id = arrange(store)
    original = read(store, item_id)

    response = await request_boundary_middleware()(
        _request(store, path, item_id, {field: {"not": "a string"}}), handler
    )

    assert response.status == 400
    assert json.loads(response.body) == expected
    assert read(store, item_id) == original
