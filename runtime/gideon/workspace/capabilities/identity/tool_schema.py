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


RULE = {"type": "object", "additionalProperties": False,
        "properties": {"type": {"enum": ["equals", "contains", "not_contains"]}, "value": TEXT, "case_sensitive": {"type": "boolean"}},
        "required": ["type", "value"]}
CASE = {"id": IDENTIFIER, "request_id": IDENTIFIER, "expected_revision": {"type": "integer", "minimum": 0}, "prompt": TEXT,
        "source_ids": {"type": "array", "items": IDENTIFIER, "minItems": 1, "maxItems": 10, "uniqueItems": True},
        "rules": {"type": "array", "items": RULE, "minItems": 1, "maxItems": 20},
        "category": {"enum": ["behavioral", "values", "boundary", "conversation"]}}
CONTRACTS.update({
    "identity_fidelity_list_cases": ({}, [], "List source-linked literal fidelity cases", False),
    "identity_fidelity_get_case": ({"id": IDENTIFIER}, ["id"], "Read an accessible fidelity case", False),
    "identity_fidelity_save_case": (CASE, ["prompt", "source_ids", "rules"], "Save explicit literal expectations against human identity sources", True),
    "identity_fidelity_list_runs": ({}, [], "List accessible recorded evaluations with honest provenance", False),
    "identity_fidelity_get_run": ({"id": IDENTIFIER}, ["id"], "Read an accessible evaluation and rule results", False),
    "identity_fidelity_observe": ({"case_id": IDENTIFIER, "answer": TEXT, "request_id": IDENTIFIER}, ["case_id", "answer", "request_id"], "Score a supplied observation; does not establish provider execution", True),
    "identity_fidelity_run": ({"case_id": IDENTIFIER, "request_id": IDENTIFIER}, ["case_id", "request_id"], "Run the configured model against source-linked literal expectations", True),
})


PLANNING = {"id": IDENTIFIER, "request_id": IDENTIFIER, "expected_revision": {"type": "integer", "minimum": 0}, "title": {"type": "string", "minLength": 1, "maxLength": 200}}
GOAL = {**PLANNING, "description": {"type": "string", "maxLength": 10000}, "status": {"enum": ["active", "completed", "archived"]}, "target_date": {"type": ["string", "null"]}}
SESSION = {**PLANNING, "goal_id": IDENTIFIER, "start_at": TEXT, "end_at": TEXT, "status": {"enum": ["scheduled", "completed", "cancelled"]}, "notes": {"type": "string", "maxLength": 10000}}
CONTRACTS.update({
    "identity_goals_list_goals": ({}, [], "List human life goals", False),
    "identity_goals_get_goal": ({"id": IDENTIFIER}, ["id"], "Read a human life goal", False),
    "identity_goals_save_goal": (GOAL, ["title", "request_id"], "Create or revise a human goal with retry protection", True),
    "identity_goals_list_sessions": ({}, [], "List human planned sessions", False),
    "identity_goals_get_session": ({"id": IDENTIFIER}, ["id"], "Read a human planned session", False),
    "identity_goals_save_session": (SESSION, ["goal_id", "title", "start_at", "end_at", "request_id"], "Schedule or revise a session, refusing overlapping plans", True),
    "identity_goals_calendar": ({}, [], "Export recorded human plans as calendar text without remote delivery", False),
})


PROGRESS = {"birth_date": {"type": ["string", "null"]}, "timezone": TEXT, "tracked_task_ids": {"type": "array", "maxItems": 100, "uniqueItems": True, "items": IDENTIFIER}, "expected_revision": {"type": "integer", "minimum": 0}, "request_id": IDENTIFIER}
CONTRACTS.update({
    "identity_progress_sheet": ({"as_of": TEXT}, [], "Project human progress from actual current goal, session, story and tracked task sources", False),
    "identity_progress_configure": (PROGRESS, list(PROGRESS), "Set human birth date, timezone and tracked native task references", True),
})


CONTRACTS.update({
    "identity_continuity_status": ({}, [], "Read heartbeat pause policy and canonical continuity slots; provider readiness remains unknown", False),
    "identity_continuity_configure": ({"heartbeat_paused": {"type": "boolean"}, "expected_revision": {"type": "integer", "minimum": 0}, "request_id": IDENTIFIER}, ["heartbeat_paused", "expected_revision", "request_id"], "Pause or resume scheduled heartbeat turns only", True),
    "identity_continuity_append_anchor": ({"slot": {"enum": ["persona", "self_notes"]}, "text": TEXT}, ["slot", "text"], "Append to a bounded existing continuity slot without resurrecting human tombstones", True),
})


CONTRACTS["identity_bundle_inventory"] = ({}, [], "List transferable continuity groups and exclusions; passphrase operations remain in human console", False)


RECIPE_STEP = {"type": "object", "additionalProperties": False, "properties": {"id": IDENTIFIER, "tool": TEXT, "arguments": {"type": "object"}}, "required": ["id", "tool", "arguments"]}
RECIPE = {"title": TEXT, "steps": {"type": "array", "items": RECIPE_STEP, "minItems": 1, "maxItems": 5}, "enabled": {"type": "boolean"}, "id": IDENTIFIER, "expected_revision": {"type": "integer", "minimum": 0}, "request_id": IDENTIFIER}
CONTRACTS.update({
    "identity_recipe_list": ({}, [], "List bounded identity read recipes", False),
    "identity_recipe_get": ({"id": IDENTIFIER}, ["id"], "Read a recipe definition", False),
    "identity_recipe_history": ({"id": IDENTIFIER}, ["id"], "Read immutable recipe revisions", False),
    "identity_recipe_save": (RECIPE, ["title", "steps", "request_id"], "Author at most five existing identity read operations with prior-output bindings", True),
    "identity_recipe_restore": ({"id": IDENTIFIER, "revision": REVISION, "expected_revision": REVISION, "request_id": IDENTIFIER}, ["id", "revision", "expected_revision", "request_id"], "Restore an earlier recipe as a new revision", True),
    "identity_recipe_begin": ({"recipe_id": IDENTIFIER, "revision": REVISION, "request_id": IDENTIFIER}, ["recipe_id", "revision", "request_id"], "Begin a pinned read recipe without dispatching steps", True),
    "identity_recipe_get_run": ({"id": IDENTIFIER}, ["id"], "Read actual step outcomes", False),
    "identity_recipe_list_runs": ({}, [], "List actual recipe run outcomes", False),
    "identity_recipe_advance": ({"run_id": IDENTIFIER, "expected_index": {"type": "integer", "minimum": 0}}, ["run_id", "expected_index"], "Dispatch one actual read step with live authority and revision rechecks", True),
    "identity_recipe_cancel": ({"run_id": IDENTIFIER}, ["run_id"], "Prevent pending recipe steps from dispatching", True),
})


MILESTONE = {"type": "object", "additionalProperties": False, "properties": {"id": IDENTIFIER, "title": TEXT, "done": {"type": "boolean"}, "target_date": {"type": ["string", "null"]}}, "required": ["id", "title", "done", "target_date"]}
GOAL_LINK = {"type": "object", "additionalProperties": False, "properties": {"kind": {"enum": ["task", "loop", "session"]}, "id": IDENTIFIER}, "required": ["kind", "id"]}
GOAL_PLAN = {"goal_id": IDENTIFIER, "parent_id": {"type": ["string", "null"]}, "horizon": {"enum": ["short_term", "long_term", "lifetime"]}, "milestones": {"type": "array", "maxItems": 100, "items": MILESTONE}, "links": {"type": "array", "maxItems": 100, "items": GOAL_LINK}, "unit": TEXT, "target_value": {"type": ["number", "null"]}, "expected_revision": {"type": "integer", "minimum": 0}, "request_id": IDENTIFIER}
CHECKIN = {"goal_id": IDENTIFIER, "value": {"type": "number"}, "observed_at": TEXT, "notes": {"type": "string", "maxLength": 5000}, "request_id": IDENTIFIER}
CONTRACTS.update({
    "identity_goal_plan_list": ({}, [], "Project human goals, hierarchy, milestones and actual linked source states", False),
    "identity_goal_plan_get": ({"goal_id": IDENTIFIER}, ["goal_id"], "Read a human goal plan and reported metric velocity", False),
    "identity_goal_plan_configure": (GOAL_PLAN, list(GOAL_PLAN), "Set human hierarchy, milestones and local activity/task/loop references", True),
    "identity_goal_plan_checkin": (CHECKIN, list(CHECKIN), "Record a human-reported metric observation; never treats automation completion as human attainment", True),
})


def definitions():
    return [ToolDefinition(name=name, provider="gideon-identity", description=description,
                           parameters={"type": "object", "properties": fields, "required": required, "additionalProperties": False},
                           requires_approval=write,
                           risk_level=RiskLevel.DESTRUCTIVE if "delete" in name else RiskLevel.CAUTION if write else RiskLevel.SAFE)
            for name, (fields, required, description, write) in CONTRACTS.items()]

CONTRACTS.update({
    "identity_lifecycle_status": ({}, [], "Read actual bound agent loop lifecycle and honest delivery receipts", False),
    "identity_lifecycle_request_thinking": ({"request_id": IDENTIFIER, "preset": {"enum": ["reflect", "review"]}}, ["request_id", "preset"], "Queue bounded self-reflection within human-approved loop authority", True),
    "identity_lifecycle_dispatch": ({"id": IDENTIFIER}, ["id"], "Dispatch one approved thinking request through the existing guarded loop delivery path", True),
})

CONTRACTS.update({
    "identity_guarded_catalog": ({}, [], "Inspect this existing native session tool permissions for guarded recipes", False),
    "identity_guarded_save": (RECIPE, ["title", "steps", "request_id"], "Author pinned cross-provider steps from this session current tool catalog", True),
    "identity_guarded_begin": CONTRACTS["identity_recipe_begin"],
    "identity_guarded_restore": CONTRACTS["identity_recipe_restore"],
    "identity_guarded_get_run": ({"id": IDENTIFIER}, ["id"], "Read this session guarded recipe receipt", False),
    "identity_guarded_advance": ({"run_id": IDENTIFIER, "expected_index": {"type": "integer", "minimum": 0}}, ["run_id", "expected_index"], "Dispatch through the existing native engine; busy current turns remain deferred", True),
    "identity_guarded_cancel": ({"run_id": IDENTIFIER}, ["run_id"], "Cancel pending guarded recipe work through its existing session", True),
})
