"""Portable groups project and restore existing canonical identity stores."""

import base64
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from gideon.core.config.document import write_configuration
from gideon.core.config.loader import AgentProfile, ConfigPreserveError
from gideon.workspace.capabilities.experience.avatar import (
    STATES,
    AvatarStore,
    avatar_info,
)
from gideon.workspace.capabilities.experience.store import ExperienceStore
from gideon.workspace.capabilities.identity.bundle_platform import (
    group_available,
    require_model_policy,
)
from gideon.workspace.capabilities.identity.store import StoryStore, _fields
from gideon.workspace.capabilities.identity.twin import TwinStore, _traits

GROUPS = ("human_twin", "autobiography", "agent_definitions", "model_policy", "avatars")
AGENT_FIELDS = (
    "description",
    "system_prompt",
    "voice",
    "natural_voice",
    "specialty",
    "route_hints",
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class BundleInventory:
    def __init__(self, home):
        self.home = Path(home)
        self.directory = self.home / "capabilities/identity"

    def _config(self):
        path = self.home / "config.json"
        value = json.loads(path.read_text()) if path.exists() else {}
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("agents", {}), dict)
            or any(
                not isinstance(row, dict) for row in value.get("agents", {}).values()
            )
        ):
            raise ValueError("Invalid destination agent configuration")
        return value

    def _avatars(self):
        return AvatarStore(ExperienceStore(self.home))

    def snapshot(self, group):
        if not group_available(group):
            raise ValueError("Group unavailable under destination policy")
        if group == "human_twin":
            return TwinStore(self.directory / "twin.sqlite3").snapshot()
        if group == "autobiography":
            return StoryStore(self.directory / "stories.sqlite3").export()
        if group == "agent_definitions":
            return [
                {
                    "name": name,
                    **{
                        key: raw.get(key, getattr(AgentProfile(), key))
                        for key in AGENT_FIELDS
                    },
                }
                for name, raw in sorted(self._config().get("agents", {}).items())
                if raw.get("source") != "builtin"
            ]
        if group == "model_policy":
            return [
                {"name": name, "model": raw["model"]}
                for name, raw in sorted(self._config().get("agents", {}).items())
                if raw.get("model")
            ]
        if group == "avatars":
            avatars = self._avatars()
            return [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "clips": row["clips"],
                    "source_hash": row["source_hash"],
                    "data": base64.b64encode(
                        avatars.raw(row["artifact_slug"], row["artifact_version"])
                    ).decode(),
                }
                for row in avatars.list()
                if row["availability"] == "ready"
            ]
        raise ValueError("Unknown identity group")

    def validate(self, group, data):
        if group not in GROUPS:
            raise ValueError("Unknown identity group")
        if group == "human_twin":
            fields = {
                "schema_version",
                "revision",
                "documents",
                "traits",
                "personas",
                "active_persona_id",
                "enabled",
            }
            if (
                not isinstance(data, dict)
                or set(data) != fields
                or data["schema_version"] != 1
                or type(data["revision"]) is not int
                or data["revision"] < 0
                or type(data["enabled"]) is not bool
            ):
                raise ValueError("Invalid human identity snapshot")
            _traits(data["traits"])
            if not isinstance(data["documents"], list) or len(data["documents"]) > 200:
                raise ValueError("Invalid human source documents")
            ids = set()
            for row in data["documents"]:
                if (
                    not isinstance(row, dict)
                    or set(row)
                    != {
                        "id",
                        "title",
                        "text",
                        "enabled",
                        "private",
                        "weight",
                        "priority",
                    }
                    or not isinstance(row["id"], str)
                    or not re.fullmatch(r"[0-9a-f]{32}", row["id"])
                    or row["id"] in ids
                ):
                    raise ValueError("Invalid human document identity")
                ids.add(row["id"])
                for key, cap in (("title", 200), ("text", 100000)):
                    if (
                        not isinstance(row[key], str)
                        or not row[key].strip()
                        or len(row[key]) > cap
                    ):
                        raise ValueError("Invalid human document text")
                if (
                    type(row["enabled"]) is not bool
                    or type(row["private"]) is not bool
                    or type(row["weight"]) is not int
                    or not 1 <= row["weight"] <= 10
                    or type(row["priority"]) is not int
                    or not 0 <= row["priority"] <= 1000
                ):
                    raise ValueError("Invalid human document controls")
            if not isinstance(data["personas"], list) or len(data["personas"]) > 20:
                raise ValueError("Invalid human persona list")
            ids = set()
            for row in data["personas"]:
                if not isinstance(row, dict) or set(row) != {
                    "id",
                    "name",
                    "instructions",
                    "trait_adjustments",
                }:
                    raise ValueError("Invalid human persona fields")
                for key, cap in (("id", 80), ("name", 200), ("instructions", 4000)):
                    if (
                        not isinstance(row[key], str)
                        or not row[key].strip()
                        or len(row[key]) > cap
                    ):
                        raise ValueError("Invalid human persona text")
                if row["id"] in ids:
                    raise ValueError("Duplicate persona")
                ids.add(row["id"])
                _traits(row["trait_adjustments"])
            if (
                data["active_persona_id"] is not None
                and data["active_persona_id"] not in ids
            ):
                raise ValueError("Missing active persona")
        elif group == "autobiography":
            if (
                not isinstance(data, dict)
                or set(data) != {"schema_version", "stories", "history"}
                or data["schema_version"] != 1
                or not isinstance(data["stories"], list)
                or not isinstance(data["history"], list)
                or len(data["history"]) > 5000
            ):
                raise ValueError("Invalid autobiography export")
            versions = set()
            for row in data["history"]:
                if (
                    not isinstance(row, dict)
                    or set(row)
                    != {
                        "id",
                        "prompt",
                        "theme",
                        "text",
                        "parent_id",
                        "created_at",
                        "updated_at",
                        "revision",
                    }
                    or not isinstance(row["id"], str)
                    or not re.fullmatch(r"[0-9a-f]{32}", row["id"])
                    or type(row["revision"]) is not int
                    or row["revision"] < 1
                ):
                    raise ValueError("Invalid autobiography revision")
                _fields(row["prompt"], row["theme"], row["text"], row["parent_id"])
                from datetime import datetime

                if any(
                    not isinstance(row[key], str)
                    or datetime.fromisoformat(row[key]).utcoffset() is None
                    for key in ("created_at", "updated_at")
                ):
                    raise ValueError("Invalid autobiography timestamp")
                pair = (row["id"], row["revision"])
                if pair in versions:
                    raise ValueError("Duplicate autobiography revision")
                versions.add(pair)
            for identifier in {item[0] for item in versions}:
                numbers = sorted(
                    revision for key, revision in versions if key == identifier
                )
                if numbers != list(range(1, len(numbers) + 1)):
                    raise ValueError(
                        "Autobiography revision history must be contiguous"
                    )
            if any(
                not isinstance(row, dict) or row not in data["history"]
                for row in data["stories"]
            ):
                raise ValueError("Invalid live autobiography row")
            live = {row["id"]: row for row in data["stories"]}
            if len(live) != len(data["stories"]):
                raise ValueError("Duplicate live story")
            for row in data["stories"]:
                if row not in data["history"]:
                    raise ValueError("Current story lacks immutable source revision")
                if row["revision"] != max(
                    revision
                    for identifier, revision in versions
                    if identifier == row["id"]
                ):
                    raise ValueError("Live story must use latest revision")
                seen, parent = {row["id"]}, row["parent_id"]
                while parent:
                    if parent not in live or parent in seen:
                        raise ValueError("Missing or cyclic story ancestry")
                    seen.add(parent)
                    parent = live[parent]["parent_id"]
        else:
            if not isinstance(data, list) or len(data) > 100:
                raise ValueError("Portable record list exceeds limit")
            seen = set()
            for row in data:
                if not isinstance(row, dict):
                    raise ValueError("Portable record must be an object")
                if group in ("agent_definitions", "model_policy"):
                    keys = (
                        {"name", *AGENT_FIELDS}
                        if group == "agent_definitions"
                        else {"name", "model"}
                    )
                    if (
                        set(row) != keys
                        or not isinstance(row["name"], str)
                        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", row["name"])
                        or row["name"] in seen
                    ):
                        raise ValueError("Invalid or duplicate agent record")
                    seen.add(row["name"])
                    for key in keys - {"name", "natural_voice"}:
                        if not isinstance(row[key], str) or len(row[key]) > 100000:
                            raise ValueError("Invalid agent text")
                    if (
                        group == "agent_definitions"
                        and type(row["natural_voice"]) is not bool
                    ):
                        raise ValueError("Invalid natural voice preference")
                else:
                    if (
                        set(row) != {"id", "title", "clips", "source_hash", "data"}
                        or not isinstance(row["title"], str)
                        or not 1 <= len(row["title"]) <= 200
                        or not isinstance(row["data"], str)
                    ):
                        raise ValueError("Invalid portable avatar")
                    raw = base64.b64decode(row["data"], validate=True)
                    info = avatar_info(raw)
                    if (
                        hashlib.sha256(raw).hexdigest() != row["source_hash"]
                        or not isinstance(row["clips"], dict)
                        or "idle" not in row["clips"]
                        or set(row["clips"]) - set(STATES)
                        or any(
                            clip not in info["animations"]
                            for clip in row["clips"].values()
                        )
                    ):
                        raise ValueError("Avatar hash or clip mapping invalid")
        return data

    def plan(self, group, data):
        self.validate(group, data)
        result = {
            "id": group,
            "new": [],
            "duplicates": [],
            "tombstoned": [],
            "conflicts": [],
            "blocked": False,
        }
        if not group_available(group):
            return {
                **result,
                "blocked": True,
                "unavailable": "Group unavailable under destination policy",
            }
        current = self.snapshot(group)
        if group == "human_twin":
            imported = {**data, "enabled": False, "revision": current["revision"]}
            if current == imported:
                result["duplicates"] = ["Human identity snapshot"]
            elif (
                current["revision"]
                or current["documents"]
                or current["traits"]
                or current["personas"]
            ):
                result["conflicts"] = [
                    "Human identity destination must be empty; existing sources are never overwritten"
                ]
            else:
                result["new"] = [
                    "Human identity snapshot (disabled; privacy flags preserved)"
                ]
        elif group == "autobiography":
            if current == data:
                result["duplicates"] = ["Autobiography chronology"]
            elif current["history"] or current["stories"]:
                result["conflicts"] = [
                    "Autobiography destination must be empty; history is never overwritten"
                ]
            else:
                result["new"] = ["Autobiography chronology"]
        elif group in ("agent_definitions", "model_policy"):
            existing = {row["name"]: row for row in current}
            names = self._config().get("agents", {})
            for row in data:
                name = row["name"]
                from gideon.engine.agents.defaults import is_reserved_agent

                if group == "agent_definitions" and is_reserved_agent(name):
                    result["conflicts"].append(
                        name + ": reserved agent cannot be imported"
                    )
                elif existing.get(name) == row:
                    result["duplicates"].append(name)
                elif group == "agent_definitions" and name in names:
                    result["conflicts"].append(
                        name + ": destination agent already exists"
                    )
                elif group == "model_policy" and name not in names:
                    result["conflicts"].append(name + ": import agent definition first")
                else:
                    result["new"].append(name)
            if group == "model_policy":
                try:
                    require_model_policy(self.home, data)
                except (ValueError, PermissionError) as error:
                    result["conflicts"].append(str(error))
        else:
            keys = {
                (
                    row["source_hash"],
                    row["title"],
                    json.dumps(row["clips"], sort_keys=True),
                )
                for row in current
            }
            for row in data:
                bucket = (
                    "duplicates"
                    if (
                        row["source_hash"],
                        row["title"],
                        json.dumps(row["clips"], sort_keys=True),
                    )
                    in keys
                    else "new"
                )
                result[bucket].append(row["title"])
        result["blocked"] = bool(result["conflicts"])
        return result

    def apply(self, group, data, expected):
        self.validate(group, data)
        current = self.snapshot(group) if group_available(group) else None
        if current is None or digest(current) != expected:
            raise ValueError("Destination group changed or became unavailable")
        plan = self.plan(group, data)
        if plan["blocked"]:
            raise ValueError("Destination group has conflicts")
        if not plan["new"]:
            return 0
        if group == "human_twin":
            store = TwinStore(self.directory / "twin.sqlite3")
            store._change(
                current["revision"],
                lambda state: state.update({**data, "enabled": False, "revision": 0}),
            )
        elif group == "autobiography":
            store = StoryStore(self.directory / "stories.sqlite3")
            with store._db() as db:
                if store._history(db) or store._list(db):
                    raise ValueError("Autobiography destination changed")
                for row in data["history"]:
                    db.execute(
                        "INSERT INTO revisions VALUES(?,?,?)",
                        (row["id"], row["revision"], json.dumps(row)),
                    )
                for row in data["stories"]:
                    db.execute(
                        "INSERT INTO stories VALUES(?,?)", (row["id"], json.dumps(row))
                    )
        elif group in ("agent_definitions", "model_policy"):
            cfg = self._config()
            agents = cfg.setdefault("agents", {})
            for row in data:
                if row["name"] not in plan["new"]:
                    continue
                if group == "agent_definitions":
                    from gideon.engine.agents.defaults import is_reserved_agent

                    if is_reserved_agent(row["name"]):
                        raise ValueError("Reserved agent cannot be imported")
                    agents[row["name"]] = asdict(
                        AgentProfile(
                            **{key: row[key] for key in AGENT_FIELDS},
                            approval_mode="interactive",
                            source="identity_bundle",
                        )
                    )
                else:
                    require_model_policy(self.home, data)
                    agents[row["name"]]["model"] = row["model"]
            write_configuration(
                self.home / "config.json", {"agents": agents}, ConfigPreserveError
            )
        else:
            avatars = self._avatars()
            for row in data:
                if row["title"] not in plan["new"]:
                    continue
                artifact = avatars.artifacts.create_binary(
                    name=row["title"],
                    data=base64.b64decode(row["data"]),
                    mime="model/gltf-binary",
                    kind="model",
                    source="manual",
                    tags=["identity_bundle"],
                    event_metadata={
                        "source_avatar_id": row["id"],
                        "source_hash": row["source_hash"],
                    },
                )
                avatars.publish(
                    {
                        "title": row["title"],
                        "artifact_slug": artifact.slug,
                        "artifact_version": artifact.version,
                        "clips": row["clips"],
                    }
                )
        return len(plan["new"])
