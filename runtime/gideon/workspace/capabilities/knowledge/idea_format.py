"""Bounded portable Markdown idea-list format, preserving ordered entries and metadata."""

import re
from datetime import datetime
from uuid import UUID

import yaml

from gideon.cognition.memory_vault import frontmatter, split_page

from .capture import CaptureError, text_field
from .reviews import digest

FIELDS = ("id", "title", "category", "status", "created", "modified", "tags")


def parse(content):
    text_field(content, "content", 262144)
    block, body = split_page(content.replace("\r\n", "\n").replace("\r", "\n"))
    if not block:
        raise CaptureError("Idea-list Markdown requires frontmatter")
    try:
        if any(
            isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken))
            for token in yaml.scan(block)
        ):
            raise ValueError("YAML aliases are not supported")
        metadata = yaml.load(block, Loader=yaml.BaseLoader)
        if (
            not isinstance(metadata, dict)
            or len(metadata) > 40
            or any(
                not isinstance(key, str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key)
                for key in metadata
            )
        ):
            raise ValueError("Invalid frontmatter fields")
        if any(
            not isinstance(value, str)
            and not (
                isinstance(value, list)
                and len(value) <= 50
                and all(isinstance(part, str) for part in value)
            )
            for value in metadata.values()
        ):
            raise ValueError("Only scalar and string-list metadata is supported")
        identity = str(UUID(metadata.get("id", metadata.get("uuid", ""))))
        created = metadata.get("created", metadata.get("createdAt", ""))
        modified = metadata.get("modified", metadata.get("updatedAt", ""))
        datetime.fromisoformat(created.replace("Z", "+00:00"))
        datetime.fromisoformat(modified.replace("Z", "+00:00"))
    except (
        ValueError,
        TypeError,
        AttributeError,
        yaml.YAMLError,
        RecursionError,
    ) as exc:
        raise CaptureError("Invalid idea-list metadata: " + str(exc)) from None
    tags = metadata.get("tags", [])
    if not isinstance(tags, list) or not any(
        tag.casefold().lstrip("#") == "idea-loom" for tag in tags
    ):
        raise CaptureError("Idea-list tags must include idea-loom")
    if metadata.get("status") not in ("draft", "completed"):
        raise CaptureError("Idea-list status must be draft or completed")
    for field, limit in (("title", 300), ("category", 100)):
        text_field(metadata.get(field), field, limit)
    lines = body.strip().splitlines()
    if not lines or not re.match(r"^#{1,6}\s+\S", lines[0]):
        raise CaptureError("Idea-list prompt heading required")
    prompt = re.sub(r"^#{1,6}\s+", "", lines.pop(0)).strip()
    if prompt.casefold() == "prompt":
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and not re.match(r"^(#|\d+\.)", lines[0].strip()):
            prompt = lines.pop(0).strip()
    text_field(prompt, "prompt", 1000)
    help_lines, ideas, section = [], [], "legacy"
    for line in lines:
        if re.fullmatch(r"#{1,6}\s+Help\s*:?", line.strip(), re.IGNORECASE):
            section = "help"
        elif re.fullmatch(r"#{1,6}\s+Ideas\s*:?", line.strip(), re.IGNORECASE):
            section = "ideas"
        elif section == "ideas" or (
            section != "help" and re.match(r"^\d+\.\s+", line.strip())
        ):
            if not line.strip():
                continue
            match = re.fullmatch(r"(\d+)\.\s+(.+)", line.strip())
            if not match or int(match[1]) != len(ideas) + 1 or len(ideas) >= 100:
                raise CaptureError(
                    "Ideas must be densely numbered from 1, at most100 entries"
                )
            text_field(match[2], "idea", 2000)
            ideas.append(match[2])
            section = "ideas"
        else:
            help_lines.append(line)
    help_text = "\n".join(help_lines).strip()
    text_field(help_text, "help", 10000, required=False)
    extra = {
        key: value
        for key, value in metadata.items()
        if key not in (*FIELDS, "uuid", "createdAt", "updatedAt")
    }
    return {
        "id": identity,
        "title": metadata["title"],
        "category": metadata["category"],
        "status": metadata["status"],
        "created": created,
        "modified": modified,
        "tags": tags,
        "prompt": prompt,
        "help": help_text,
        "ideas": ideas,
        "extra": extra,
    }


def render(document):
    fields = [(key, document[key]) for key in FIELDS] + list(
        document.get("extra", {}).items()
    )
    lines = [frontmatter(fields), "", "# " + document["prompt"]]
    if document["help"]:
        lines += ["", "## Help", document["help"]]
    if document["ideas"]:
        lines += [
            "",
            "## Ideas",
            *[f"{index + 1}. {text}" for index, text in enumerate(document["ideas"])],
        ]
    return "\n".join(lines) + "\n"


def preview(content):
    document = parse(content)
    return {
        "preview_id": digest(document),
        "document": document,
        "unsupported_fields": sorted(document["extra"]),
    }
