"""Strict owner-question forms and their original wire values."""

from __future__ import annotations

import re

from gideon.assurance.validation import (
    _AUQ_LABEL_CAP,
    _AUQ_MAX_OPTIONS,
    _AUQ_MAX_QUESTIONS,
    _AUQ_TEXT_CAP,
)

CANCEL = {"action": "cancel"}
_KEY = re.compile(r"question_(0|[1-9][0-9]*)$")


def questions_from_form(params):
    if not isinstance(params, dict) or params.get("mode", "form") != "form":
        return None
    schema = params.get("requestedSchema")
    if (
        not isinstance(schema, dict)
        or schema.get("type") != "object"
        or set(schema)
        - {
            "type",
            "properties",
            "required",
            "additionalProperties",
            "$schema",
            "title",
            "description",
        }
    ):
        return None
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return None
    keys = sorted(
        (key for key in props if isinstance(key, str) and _KEY.fullmatch(key)),
        key=lambda key: int(key.split("_")[1]),
    )
    if not 1 <= len(keys) <= _AUQ_MAX_QUESTIONS or set(props) - set(keys) - {
        key + "_custom" for key in keys
    }:
        return None
    required = schema.get("required", [])
    if (
        not isinstance(required, list)
        or any(not isinstance(key, str) or key not in keys for key in required)
        or len(set(required)) != len(required)
    ):
        return None
    questions = []
    for key in keys:
        field = props[key]
        if (
            not isinstance(field, dict)
            or set(field)
            - {"type", "items", "oneOf", "title", "description", "uniqueItems"}
            or ("uniqueItems" in field and field["uniqueItems"] is not True)
        ):
            return None
        multi = field.get("type") == "array"
        if multi:
            items = field.get("items")
            options = (
                items.get("anyOf")
                if isinstance(items, dict) and not set(items) - {"anyOf"}
                else None
            )
        elif field.get("type") == "string":
            options = field.get("oneOf")
        else:
            return None
        if not isinstance(options, list) or not 1 <= len(options) <= _AUQ_MAX_OPTIONS:
            return None
        parsed = []
        for option in options:
            if (
                not isinstance(option, dict)
                or set(option) - {"const", "title", "description", "type"}
                or option.get("type", "string") != "string"
                or not isinstance(option.get("const"), str)
                or not option["const"]
            ):
                return None
            label = option.get("title", option["const"])
            description = option.get("description", "")
            if (
                not isinstance(label, str)
                or not label.strip()
                or not isinstance(description, str)
            ):
                return None
            parsed.append(
                {
                    "value": option["const"],
                    "label": label.strip()[:_AUQ_LABEL_CAP],
                    "description": description[:_AUQ_TEXT_CAP],
                }
            )
        if len({option["value"] for option in parsed}) != len(parsed):
            return None
        text = field.get("description") or (
            params.get("message") if len(keys) == 1 else ""
        )
        header = field.get("title", "")
        custom = props.get(key + "_custom")
        if not isinstance(text, str) or not text.strip() or not isinstance(header, str):
            return None
        if custom is not None and (
            not isinstance(custom, dict)
            or custom.get("type") != "string"
            or set(custom) - {"type", "title", "description"}
        ):
            return None
        questions.append(
            {
                "key": key,
                "question": text.strip()[:_AUQ_TEXT_CAP],
                "header": header[:_AUQ_LABEL_CAP],
                "multiSelect": multi,
                "free_text": custom is not None,
                "options": parsed,
            }
        )
    return questions


def form_response(questions, outcome):
    kind, answers, _ = outcome
    if kind == "skipped":
        return {"action": "decline"}
    if (
        kind != "answered"
        or not isinstance(answers, list)
        or len(answers) != len(questions)
    ):
        return dict(CANCEL)
    content = {}
    for question, (selected, other) in zip(questions, answers):
        if (
            any(
                type(index) is not int or index < 0 or index >= len(question["options"])
                for index in selected
            )
            or (not question["multiSelect"] and len(selected) > 1)
            or (other and not question["free_text"])
        ):
            return dict(CANCEL)
        chosen = [question["options"][index]["value"] for index in selected]
        if chosen:
            content[question["key"]] = chosen if question["multiSelect"] else chosen[0]
        if other:
            content[question["key"] + "_custom"] = other
    return {"action": "accept", "content": content}


def attended_owner(session_key):
    from gideon.security.owner_questions import QuestionRefused, registry

    current = registry()
    if current is None or not session_key:
        return False
    try:
        current._admit(session_key)
    except QuestionRefused:
        return False
    return True


async def answer_owner_form(params, *, session_key, call_id):
    from gideon.security.owner_questions import QuestionRefused, registry

    questions = questions_from_form(params)
    current = registry()
    if questions is None or current is None or not attended_owner(session_key):
        return dict(CANCEL)
    try:
        outcome = await current.ask_outcome(
            session_key, {"questions": questions}, call_id=call_id
        )
    except QuestionRefused:
        return dict(CANCEL)
    return form_response(questions, outcome)
