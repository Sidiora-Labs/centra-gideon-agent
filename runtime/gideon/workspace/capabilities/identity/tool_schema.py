"""Discoverable contracts for human identity operations."""
from gideon.integrations.tool_providers.base import RiskLevel, ToolDefinition

TEXT = {"type": "string", "minLength": 1}
IDENTIFIER = {"type": "string", "minLength": 1, "maxLength": 128}
REVISION = {"type": "integer", "minimum": 1}
STORY = {"prompt": {**TEXT, "maxLength": 4000}, "theme": {**TEXT, "maxLength": 200},
         "text": {**TEXT, "maxLength": 100000}, "parent_id": {"type": ["string", "null"]}}
DOCUMENT = {"title": {**TEXT, "maxLength": 200}, "text": {**TEXT, "maxLength": 100000},
            "id": IDENTIFIER, "enabled": {"type": "boolean"}, "weight": {"type": "integer", "minimum": 1, "maximum": 10},
            "priority": {"type": "integer", "minimum": 0, "maximum": 1000},
            "expected_revision": {"type": "integer", "minimum": 0}}
TRAITS = {"type": "object", "maxProperties": 40, "additionalProperties": {"type": ["string", "number"]}}
PERSONA = {"type": "object", "additionalProperties": False,
           "properties": {"id": IDENTIFIER, "name": TEXT, "instructions": TEXT, "trait_adjustments": TRAITS},
           "required": ["id", "name", "instructions", "trait_adjustments"]}
CONFIG = {"expected_revision": {"type": "integer", "minimum": 0}, "enabled": {"type": "boolean"},
          "traits": TRAITS, "personas": {"type": "array", "maxItems": 20, "items": PERSONA},
          "active_persona_id": {"type": ["string", "null"]}}
CONTRACTS = {
    "identity_story_list": ({}, [], "List authored life stories", False),
    "identity_story_get": ({"story_id": IDENTIFIER}, ["story_id"], "Read an authored story", False),
    "identity_story_create": ({**STORY, "request_id": IDENTIFIER}, ["prompt", "theme", "text", "request_id"], "Create an authored answer with a retry identifier", True),
    "identity_story_update": ({**STORY, "story_id": IDENTIFIER, "expected_revision": REVISION}, ["story_id", "expected_revision", "prompt", "theme", "text"], "Edit an answer while preserving earlier revisions", True),
    "identity_story_delete": ({"story_id": IDENTIFIER, "expected_revision": REVISION}, ["story_id", "expected_revision"], "Delete a leaf story, retaining answer history", True),
    "identity_story_chain": ({"story_id": IDENTIFIER}, ["story_id"], "Read the complete family of follow-up stories", False),
    "identity_story_history": ({"story_id": IDENTIFIER}, ["story_id"], "Read immutable answer revisions", False),
    "identity_story_export": ({}, [], "Export complete authored chronology and revisions", False),
    "identity_twin_get": ({}, [], "Read public-to-agent identity sources and configuration", False),
    "identity_twin_save_document": (DOCUMENT, ["title", "text", "expected_revision"], "Save a non-private identity source", True),
    "identity_twin_delete_document": ({"id": IDENTIFIER, "expected_revision": {"type": "integer", "minimum": 0}}, ["id", "expected_revision"], "Delete a non-private identity source", True),
    "identity_twin_configure": (CONFIG, list(CONFIG), "Save human traits and persona overlay settings", True),
    "identity_twin_context": ({"budget": {"type": "integer", "minimum": 1, "maximum": 10000}}, [], "Preview bounded identity context without private sources", False),
    "identity_twin_enrich": ({"document_id": IDENTIFIER}, ["document_id"], "Ask the configured provider for follow-up questions about an enabled non-private source", True),
}


def definitions():
    return [ToolDefinition(name=name, provider="gideon-identity", description=description,
                           parameters={"type": "object", "properties": fields, "required": required, "additionalProperties": False},
                           requires_approval=write,
                           risk_level=RiskLevel.DESTRUCTIVE if "delete" in name else RiskLevel.CAUTION if write else RiskLevel.SAFE)
            for name, (fields, required, description, write) in CONTRACTS.items()]
