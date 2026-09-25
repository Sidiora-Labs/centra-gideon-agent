"""Native tools over the authoritative creative catalog and moodboards."""
import json
from copy import deepcopy

from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult
from .moodboards import BoardStore
from .universes import UniverseStore
from .store import CatalogError, IngredientStore, TYPES, integer, keys


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


STRING = {"type": "string"}
NUMBER = {"type": "integer", "minimum": 1}
PAGE = {"q": STRING, "offset": {"type": "integer", "minimum": 0}, "limit": {**NUMBER, "maximum": 100}}
REFERENCE = obj({"kind": {"enum": ["artifact", "knowledge"]}, "id": STRING}, ["kind", "id"])
RELATION = obj({"kind": {"enum": ["related", "contains"]}, "target_id": STRING}, ["kind", "target_id"])
INGREDIENT = {"title": STRING, "type": {"enum": list(TYPES)}, "body": STRING,
              "tags": {"type": "array", "items": STRING},
              "source_refs": {"type": "array", "items": REFERENCE},
              "relations": {"type": "array", "items": RELATION}}
CARD = obj({"id": STRING, "artifact_id": STRING, "artifact_version": NUMBER, "caption": STRING,
            "colors": {"type": "array", "items": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}}},
           ["id", "artifact_id", "artifact_version"])
GROUP = obj({"id": STRING, "title": STRING, "cards": {"type": "array", "items": CARD}}, ["id", "title"])
BOARD = {"title": STRING, "groups": {"type": "array", "items": GROUP},
         "ingredient_ids": {"type": "array", "items": STRING}}


UNIVERSE = {"title": STRING, "canon": {"type": "array", "items": obj({"id": STRING, "title": STRING, "body": STRING}, ["id", "title"])},
            "visual_identity": obj({"colors": {"type": "array", "items": STRING}, "style_notes": STRING}),
            "ingredient_ids": {"type": "array", "items": STRING},
            "board_refs": {"type": "array", "items": obj({"id": STRING, "revision": NUMBER}, ["id", "revision"])}}


def schemas():
    result = {}
    for entity, fields in (("ingredient", INGREDIENT), ("board", BOARD), ("universe", UNIVERSE)):
        result[f"creative_{entity}_list"] = obj({**PAGE, **({"type": {"enum": list(TYPES)}, "tag": STRING} if entity == "ingredient" else {})})
        result[f"creative_{entity}_get"] = obj({"id": STRING}, ["id"])
        result[f"creative_{entity}_create"] = obj({"payload": obj({**fields, "request_id": STRING}, ["title", "request_id", *(["type"] if entity == "ingredient" else [])])}, ["payload"])
        result[f"creative_{entity}_update"] = obj({"id": STRING, "payload": obj({**fields, "revision": NUMBER}, ["revision"])}, ["id", "payload"])
        result[f"creative_{entity}_revisions"] = obj({"id": STRING, "offset": PAGE["offset"], "limit": PAGE["limit"]}, ["id"])
        result[f"creative_{entity}_restore"] = obj({"id": STRING, "payload": obj({"revision": NUMBER, "target_revision": NUMBER}, ["revision", "target_revision"])}, ["id", "payload"])
    result["creative_board_export"] = obj({"id": STRING, "revision": NUMBER}, ["id"])
    result["creative_universe_export"] = obj({"id": STRING, "revision": NUMBER}, ["id"])
    result["creative_board_sources"] = obj({"q": STRING})
    return result


SCHEMAS = schemas()
WRITES = {"create", "update", "restore"}


class CreativeToolProvider(ToolProvider):
    def __init__(self, home=None):
        self.ingredients = IngredientStore(home)
        self.boards = BoardStore(self.ingredients.home)
        self.universes = UniverseStore(self.ingredients.home)

    @property
    def name(self):
        return "gideon-creative"

    @property
    def display_name(self):
        return "Creative catalog and moodboards"

    async def list_tools(self):
        return [ToolDefinition(name=name, provider=self.name,
            description=(f"{name.removeprefix('creative_').replace('_', ' ').capitalize()} in the current runtime creative library. "
                         "Mutations require the current revision; create requires a unique request_id. "
                         "Use board sources for canonical artifact IDs and versions. No generated images."),
            parameters=deepcopy(schema), requires_approval=name.rsplit("_", 1)[1] in WRITES,
            risk_level=RiskLevel.CAUTION if name.rsplit("_", 1)[1] in WRITES else RiskLevel.SAFE)
            for name, schema in SCHEMAS.items()]

    async def invoke(self, tool_name, arguments):
        try:
            if tool_name not in SCHEMAS:
                raise CatalogError("Unknown creative tool", 404)
            schema = SCHEMAS[tool_name]
            keys(arguments, set(schema["properties"]))
            if set(schema["required"]) - set(arguments):
                raise CatalogError("Missing required arguments")
            _, entity, action = tool_name.split("_")
            store = {"ingredient": self.ingredients, "board": self.boards, "universe": self.universes}[entity]
            args = dict(arguments)
            if action == "revisions":
                offset = integer(args.get("offset", 0), 0, 1000000)
                limit = integer(args.get("limit", 25), 1, 100)
                records = store.revisions(args["id"])
                result = {"items": records[offset:offset + limit], "total": len(records), "offset": offset, "limit": limit}
            elif action in WRITES:
                result = getattr(store, action)(args["id"], args["payload"]) if action != "create" else store.create(args["payload"])
            else:
                result = getattr(store, action)(**args)
                if entity == "ingredient" and action == "get":
                    result = {**result, "source_status": store.source_status(result)}
            return ToolResult(success=True, output=json.dumps(result, ensure_ascii=False), metadata={"entity": entity, "action": action})
        except CatalogError as exc:
            return ToolResult(success=False, error=str(exc), metadata={"status": exc.status})


def create_provider(config=None):
    if config is not None:
        keys(config, set())
    return CreativeToolProvider()
