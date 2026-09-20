"""Document-comment CRUD endpoints."""

from aiohttp import web

from gideon.core.http_request import RequestValidationError, read_json_body
from gideon.interfaces.dashboard import doc_comments

_CREATE_FIELDS = (
    "docId",
    "docLabel",
    "docPath",
    "quote",
    "comment",
    "line",
    "column",
    "context",
)


async def api_doc_comments(request: web.Request) -> web.Response:
    if request.method == "GET":
        return web.json_response({"comments": doc_comments.list_comments()})
    try:
        body = await read_json_body(request)
        for field in ("docId", "docLabel", "quote", "comment"):
            if not isinstance(body.get(field), str) or not body[field].strip():
                raise RequestValidationError(f"{field} is required")
        comment = doc_comments.create_comment(
            {field: body[field] for field in _CREATE_FIELDS if field in body}
        )
    except RequestValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"comment": comment}, status=201)


async def api_doc_comment(request: web.Request) -> web.Response:
    comment_id = request.match_info["comment_id"]
    if request.method == "PATCH":
        try:
            body = await read_json_body(request)
            text = body.get("comment")
            if not isinstance(text, str) or not text.strip():
                raise RequestValidationError("comment is required")
        except RequestValidationError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        comment = doc_comments.update_comment(comment_id, text)
        if comment is None:
            return web.json_response({"error": "comment not found"}, status=404)
        return web.json_response({"comment": comment})
    if not doc_comments.delete_comments({comment_id}):
        return web.json_response({"error": "comment not found"}, status=404)
    return web.json_response({"ok": True})


async def api_doc_comments_bulk_delete(request: web.Request) -> web.Response:
    try:
        body = await read_json_body(request)
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(
            isinstance(value, str) for value in ids
        ):
            raise RequestValidationError("ids must be an array of strings")
    except RequestValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"deleted": doc_comments.delete_comments(set(ids))})


def setup_doc_comment_routes(app: web.Application) -> None:
    app.router.add_get("/api/doc-comments", api_doc_comments)
    app.router.add_post("/api/doc-comments", api_doc_comments)
    app.router.add_patch("/api/doc-comments/{comment_id}", api_doc_comment)
    app.router.add_delete("/api/doc-comments/{comment_id}", api_doc_comment)
    app.router.add_delete("/api/doc-comments", api_doc_comments_bulk_delete)
