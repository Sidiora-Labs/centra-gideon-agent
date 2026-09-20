from gideon.core.http_request import read_json_body

"""Vocabulary request decoding, record projection and mutation responses."""

from dataclasses import dataclass

from gideon.cognition.lexicon.vocabulary_flow import KnowledgeVocabulary


@dataclass(frozen=True)
class RequestDocument:
    value: object
    valid: bool

    @classmethod
    async def read(cls, request):
        try:
            return cls(await read_json_body(request), True)
        except Exception:
            return cls(None, False)


class VocabularyHttp:
    term_fields = (
        "id",
        "canonical",
        "aliases",
        "entity_type",
        "weight",
        "source",
        "enabled",
    )
    correction_fields = ("id", "heard", "meant", "count", "auto_apply", "last_seen")

    @staticmethod
    def project(record, fields):
        return {field: getattr(record, field) for field in fields}

    @staticmethod
    def invalid(api):
        return api.web.json_response({"error": "invalid JSON"}, status=400)

    @staticmethod
    def success(api, **values):
        return api.web.json_response({"ok": True, **values})

    @classmethod
    async def add(cls, api, request, kind):
        document = await RequestDocument.read(request)
        if not document.valid:
            return cls.invalid(api)
        body = document.value
        if kind == "term":
            canonical = (body.get("canonical") or "").strip()
            if not canonical:
                return api.web.json_response(
                    {"error": "canonical is required"}, status=400
                )
            aliases = body.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [part.strip() for part in aliases.split(",") if part.strip()]
            service = api.get_lexicon_service()
            identity = service.add_manual_term(canonical, aliases=list(aliases))
            return cls.success(api, id=identity)
        heard, meant = (body.get("heard") or "").strip(), (
            body.get("meant") or ""
        ).strip()
        if not heard or not meant:
            return api.web.json_response(
                {"error": "heard and meant are required"}, status=400
            )
        service = api.get_lexicon_service()
        service.learn_correction(heard, meant, always=bool(body.get("always")))
        return cls.success(api)

    @classmethod
    async def update(cls, api, request, property_name, operation, missing):
        identity = request.match_info["id"]
        document = await RequestDocument.read(request)
        if not document.valid:
            return cls.invalid(api)
        service = api.get_lexicon_service()
        if property_name not in document.value:
            return cls.success(api)
        apply = getattr(service.store, operation)
        accepted = apply(identity, bool(document.value[property_name]))
        if accepted:
            return cls.success(api)
        return api.web.json_response({"error": missing}, status=404)

    @classmethod
    def delete(cls, api, request):
        service = api.get_lexicon_service()
        removed = service.store.delete_term(request.match_info["id"])
        if removed:
            return cls.success(api)
        return api.web.json_response({"error": "term not found"}, status=404)

    @staticmethod
    def terms(api, request):
        service = api.get_lexicon_service()
        criteria = {name: request.query.get(name, "") for name in ("source", "search")}
        selected = service.list_terms(**criteria)
        payload = dict(
            terms=[api._term_dict(term) for term in selected],
            total=service.store.count_terms(),
        )
        return api.web.json_response(payload)

    @staticmethod
    def corrections(api):
        service = api.get_lexicon_service()
        values = service.list_corrections()
        return api.web.json_response(
            {"corrections": [api._corr_dict(value) for value in values]}
        )

    @classmethod
    def rebuild(cls, api):
        try:
            snapshot = KnowledgeVocabulary.snapshot()
        except Exception:
            api.logger.warning(
                "lexicon rebuild: could not read entities", exc_info=True
            )
            return api.web.json_response(
                {"error": "could not read knowledge entities"}, status=500
            )
        service = api.get_lexicon_service()
        written = service.rebuild_from_graph(snapshot)
        return cls.success(api, synced=written, total=service.store.count_terms())

    @classmethod
    async def reset(cls, api, request):
        document = (
            await RequestDocument.read(request)
            if request.can_read_body
            else RequestDocument({}, True)
        )
        value = document.value
        confirmed = isinstance(value, dict) and value.get("confirm")
        if confirmed:
            api.get_lexicon_service().store.reset()
            return cls.success(api)
        return api.web.json_response(
            {
                "error": {
                    "code": "confirm_required",
                    "message": "reset drops every term and every user-authored correction — pass confirm: true",
                }
            },
            status=400,
        )

    @staticmethod
    def routes(api, app):
        endpoints = (
            ("get", "/terms", api.api_lexicon_terms),
            ("post", "/terms", api.api_lexicon_add_term),
            ("patch", "/terms/{id}", api.api_lexicon_update_term),
            ("delete", "/terms/{id}", api.api_lexicon_delete_term),
            ("post", "/rebuild", api.api_lexicon_rebuild),
            ("get", "/corrections", api.api_lexicon_corrections),
            ("post", "/corrections", api.api_lexicon_add_correction),
            ("patch", "/corrections/{id}", api.api_lexicon_update_correction),
            ("post", "/reset", api.api_lexicon_reset),
        )
        for method, suffix, handler in endpoints:
            getattr(app.router, "add_" + method)("/api/lexicon" + suffix, handler)
