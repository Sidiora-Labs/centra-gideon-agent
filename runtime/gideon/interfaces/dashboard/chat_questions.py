"""Authenticated owner-only question answer routes."""
from aiohttp import web
from gideon.core.http_request import read_json_body
from gideon.security.approval_answer import OWNER, of_request
from gideon.security.owner_questions import QuestionRefused


async def api_question_answer(request):
    principal = of_request(request)
    if principal.kind != OWNER:
        return web.json_response({"error": {"code": "owner_required"}}, status=403)
    body = await read_json_body(request)
    try:
        state = request.app["state"]
        state.owner_questions.answer(str(body.get("id") or ""), str(body.get("session") or ""), principal,
                                     body.get("answers"), body.get("skip", False))
    except QuestionRefused as error:
        return web.json_response({"error": {"code": error.code, "message": str(error)}}, status=400)
    return web.json_response({"ok": True})


async def api_question_pending(request):
    state, principal = request.app["state"], of_request(request)
    key = str(request.query.get("session") or "")
    session = state.get_session(key)
    if principal.kind != OWNER or session is None or session._initiator != {"kind": principal.kind, "name": principal.name, "tenant": principal.tenant}:
        return web.json_response({"error": {"code": "owner_required"}}, status=403)
    state.owner_questions.expire_orphans(session)
    return web.json_response({"questions": state.owner_questions.pending_for(key)})
