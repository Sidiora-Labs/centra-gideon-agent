"""Ordered projections, tolerant scalar decoding and structural workflow traversal."""


def contract():
    from gideon.automation.workflows import models

    return models


class Fields:
    def __init__(self, payload):
        self.payload = payload

    def raw(self, name, default=None):
        return self.payload.get(name, default)

    def scalar(self, name, conversion=str, default=""):
        return conversion(self.payload.get(name, default) or default)

    def strings(self, name):
        return [str(value) for value in (self.payload.get(name) or [])]

    def mapping(self, name):
        return dict(self.payload.get(name) or {})

    def enum(self, name, enum_type, default, *, fill_empty=True, catch_conversion=True):
        value = self.payload.get(name, default)
        if fill_empty:
            value = value or default
        if not catch_conversion:
            value = str(value)
        try:
            return enum_type(str(value))
        except ValueError:
            return enum_type(default)

    def extras(self, known):
        return {
            name: value for name, value in self.payload.items() if name not in known
        }


class Projection:
    conversions = {
        "raw": lambda value: value,
        "enum": lambda value: value.value,
        "list": list,
        "map": dict,
        "record": lambda value: value.to_dict(),
        "maybe_record": lambda value: value.to_dict() if value else None,
        "records": lambda values: {
            name: value.to_dict() for name, value in values.items()
        },
        "list_maps": lambda values: [dict(value) for value in values],
        "map_lists": lambda values: {
            name: list(value) for name, value in values.items()
        },
    }

    def __init__(self, *columns):
        self.columns = columns

    def write(self, record, *, extra=False):
        output = {}
        for column in self.columns:
            if isinstance(column, str):
                name, attribute, conversion = column, column, "raw"
            else:
                name, attribute, conversion = column
            output[name] = self.conversions[conversion](getattr(record, attribute))
        if extra:
            output.update(record.extra)
        return output


class TreeShape:
    @staticmethod
    def edges(node):
        for index, child in enumerate(node.children):
            yield f"children[{index}]", child
        if node.body is not None:
            yield "body", node.body
        for name, case in node.cases.items():
            yield f"cases[{name}]", case
        if node.default_case is not None:
            yield "default", node.default_case

    @classmethod
    def descendants(cls, node, path):
        yield path, node
        for suffix, child in cls.edges(node):
            yield from cls.descendants(child, f"{path}.{suffix}")

    @staticmethod
    def write(node):
        result = {"kind": node.kind.value}
        if node.id:
            result["id"] = node.id
        optional = (
            (
                "children",
                "children",
                lambda values: [child.to_dict() for child in values],
                bool,
            ),
            (
                "body",
                "body",
                lambda child: child.to_dict(),
                lambda child: child is not None,
            ),
            (
                "cases",
                "cases",
                lambda values: {key: child.to_dict() for key, child in values.items()},
                bool,
            ),
            (
                "default",
                "default_case",
                lambda child: child.to_dict(),
                lambda child: child is not None,
            ),
            ("config", "config", dict, bool),
            ("needs", "needs", list, bool),
        )
        for key, attribute, encode, present in optional:
            value = getattr(node, attribute)
            if present(value):
                result[key] = encode(value)
        result.update(node.extra)
        return result

    @staticmethod
    def read(factory, payload):
        api = contract()
        raw_kind = str((payload or {}).get("kind", "")).strip()
        try:
            kind = api.NodeKind(raw_kind)
        except ValueError as exc:
            raise ValueError(f"unknown node kind {raw_kind!r}") from exc
        body, default = payload.get("body"), payload.get("default")
        row = Fields(payload)
        values = dict(kind=kind, id=row.scalar("id"))
        values["children"] = [
            factory.from_dict(child) for child in (payload.get("children") or [])
        ]
        values["body"] = factory.from_dict(body) if isinstance(body, dict) else None
        values["cases"] = {
            name: factory.from_dict(child)
            for name, child in (payload.get("cases") or {}).items()
        }
        values["default_case"] = (
            factory.from_dict(default) if isinstance(default, dict) else None
        )
        values.update(
            config=row.mapping("config"),
            needs=row.strings("needs"),
            extra=Fields(payload or {}).extras(factory._KNOWN),
        )
        return factory(**values)


class RecordCodecs:
    layouts = {
        "Failure": Projection(
            ("class", "failure_class", "enum"),
            "cause_plain",
            "remediation",
            "recoverable",
            "retryable",
            "terminal_reason",
            "suggestion",
        ),
        "FailureSignature": Projection(
            "failing_node", "stage", "layer", "reason", "input_hash"
        ),
        "InputParam": Projection("type", "required", "default", "help"),
        "RunBudget": Projection("max_tokens", "max_cost", "max_retries"),
        "RunDefaults": Projection(
            "model_tier",
            "effort",
            "max_concurrency",
            "node_timeout_total_secs",
            "node_timeout_stall_secs",
            ("budget", "budget", "record"),
        ),
        "DefMetadata": Projection(
            "risk",
            ("capabilities", "capabilities", "list"),
            ("requirements", "requirements", "map_lists"),
            ("steering_examples", "steering_examples", "list_maps"),
            ("keywords", "keywords", "list"),
            ("example_outputs", "example_outputs", "list"),
            ("shapes", "shapes", "list"),
            "when_not_to_use",
            "lighter_path",
            ("presets", "presets", "list"),
            "match_text",
            "surface_mode",
            "agent_digest",
            "summary",
            "when_to_use",
            "cadence_days",
            "escalation",
            ("packs", "packs", "list"),
            ("hands_off_to", "hands_off_to", "list_maps"),
            "guided",
            "a2a_published",
        ),
        "WorkflowDef": Projection(
            "name",
            "version",
            "spec_semver",
            "description",
            "source",
            "provenance",
            ("inputs", "inputs", "records"),
            ("defaults", "defaults", "record"),
            ("metadata", "metadata", "record"),
            ("on_overlap", "on_overlap", "enum"),
            ("root", "root", "record"),
            ("tags", "tags", "list"),
            ("runtime_hints", "runtime_hints", "map"),
            "created_at",
            "updated_at",
        ),
        "RunOrigin": Projection(
            ("kind", "kind", "enum"), "session_key", "tool_call_id", "trigger_id"
        ),
        "WorkflowRun": Projection(
            "id",
            "workflow_name",
            ("status", "status", "enum"),
            "spec_version",
            ("inputs", "inputs", "map"),
            "intent",
            ("origin", "origin", "record"),
            "parent_run_id",
            "root_run_id",
            "spawned_by_node_id",
            "branch_key",
            "forked_from",
            "project_id",
            "mode",
            ("budget", "budget", "record"),
            "pinned",
            "created_at",
            "started_at",
            "completed_at",
            "elapsed_seconds",
            "total_tokens",
            "agent_count",
            "error_message",
            "attention",
            ("policy_overrides", "policy_overrides", "map"),
            "owner_username",
            "origin_harness",
        ),
        "NodeInstance": Projection(
            "path",
            ("state", "state", "enum"),
            "epoch",
            "attempt",
            ("declined_edges", "declined_edges", "list"),
            "degraded_reason",
            ("failure", "failure", "maybe_record"),
            "started_at",
            "completed_at",
            "output_ref",
            "tokens",
            "wake_at",
            "item_label",
            "item_total",
            "subagent_id",
        ),
    }

    @classmethod
    def write(cls, name, record):
        return cls.layouts[name].write(
            record, extra=name in ("WorkflowDef", "WorkflowRun")
        )

    @staticmethod
    def failure(factory, payload):
        api = contract()
        classification = Fields(payload or {}).enum(
            "class",
            api.FailureClass,
            "internal",
            fill_empty=False,
            catch_conversion=False,
        )
        row = Fields(payload)
        values = dict(
            failure_class=classification,
            cause_plain=row.scalar("cause_plain"),
            remediation=row.scalar("remediation"),
            recoverable=bool(row.raw("recoverable", False)),
            terminal_reason=row.scalar("terminal_reason"),
            suggestion=row.scalar("suggestion"),
        )
        return factory(**values)

    @staticmethod
    def input(factory, payload):
        kind = Fields(payload or {}).scalar("type", default="string")
        row = Fields(payload)
        return factory(
            type=kind,
            required=bool(row.raw("required", False)),
            default=row.raw("default"),
            help=row.scalar("help"),
        )

    @staticmethod
    def budget(factory, payload):
        row = Fields(payload or {})
        values = {}
        for name, conversion, fallback in (
            ("max_tokens", int, 0),
            ("max_cost", float, 0.0),
            ("max_retries", int, 3),
        ):
            values[name] = row.scalar(name, conversion, fallback)
        return factory(**values)

    @staticmethod
    def defaults(factory, payload):
        api, row = contract(), Fields(payload or {})
        values = dict(
            model_tier=row.scalar("model_tier", default="standard"),
            effort=row.scalar("effort"),
        )
        for name in (
            "max_concurrency",
            "node_timeout_total_secs",
            "node_timeout_stall_secs",
        ):
            values[name] = row.scalar(name, int, 0)
        values["budget"] = api.RunBudget.from_dict(row.raw("budget") or {})
        return factory(**values)

    @staticmethod
    def metadata(factory, payload):
        api, row = contract(), Fields(payload or {})
        requirements = row.raw("requirements") or {}
        values = dict(risk=row.scalar("risk", default="low"))
        for name in ("capabilities", "keywords", "example_outputs", "shapes"):
            values[name] = row.strings(name)
        for name in ("when_not_to_use", "lighter_path"):
            values[name] = row.scalar(name)
        values["presets"], values["match_text"] = row.strings("presets"), row.scalar(
            "match_text"
        )
        values["surface_mode"] = api._surface_mode(row.raw("surface_mode"))
        for name in ("agent_digest", "summary", "when_to_use"):
            values[name] = row.scalar(name)
        values["cadence_days"] = api._non_negative_int(row.raw("cadence_days"))
        values["escalation"] = api._escalation(row.raw("escalation"))
        values["packs"] = row.strings("packs")
        values["hands_off_to"] = [
            dict(item)
            for item in (row.raw("hands_off_to") or [])
            if isinstance(item, dict)
        ]
        for name in ("guided", "a2a_published"):
            values[name] = row.raw(name) is True
        values["requirements"] = {
            str(name): [str(value) for value in (items or [])]
            for name, items in (
                requirements.items() if isinstance(requirements, dict) else []
            )
        }
        values["steering_examples"] = [
            {str(name): str(value) for name, value in item.items()}
            for item in (row.raw("steering_examples") or [])
            if isinstance(item, dict)
        ]
        return factory(**values)

    @staticmethod
    def definition(factory, payload):
        api, row = contract(), Fields(payload or {})
        overlap = row.enum(
            "on_overlap", api.OverlapPolicy, "skip", catch_conversion=False
        )
        root = row.raw("root")
        if not isinstance(root, dict):
            raise ValueError("workflow def has no root node")
        values = dict(
            name=row.scalar("name"),
            root=api.Node.from_dict(root),
            version=row.scalar("version", int, 1),
            spec_semver=row.scalar("spec_semver", default=api.SPEC_SEMVER),
        )
        values["description"] = row.scalar("description")
        for name in ("source", "provenance"):
            values[name] = row.scalar(name, default="user")
        values["inputs"] = {
            str(name): api.InputParam.from_dict(item)
            for name, item in (row.raw("inputs") or {}).items()
        }
        values["defaults"] = api.RunDefaults.from_dict(row.raw("defaults") or {})
        values["metadata"] = api.DefMetadata.from_dict(row.raw("metadata") or {})
        values["on_overlap"], values["tags"] = overlap, row.strings("tags")
        values["runtime_hints"] = (
            dict(row.payload["runtime_hints"])
            if isinstance(row.raw("runtime_hints"), dict)
            else {}
        )
        values.update(
            created_at=row.scalar("created_at"),
            updated_at=row.scalar("updated_at"),
            extra=row.extras(factory._KNOWN),
        )
        return factory(**values)

    @staticmethod
    def origin(factory, payload):
        api, row = contract(), Fields(payload or {})
        values = dict(kind=row.enum("kind", api.OriginKind, "manual"))
        for name in ("session_key", "tool_call_id", "trigger_id"):
            values[name] = row.scalar(name)
        return factory(**values)

    @staticmethod
    def run(factory, payload):
        api, row = contract(), Fields(payload or {})
        status = row.enum("status", api.RunStatus, "draft")
        values = dict(
            id=row.scalar("id"),
            workflow_name=row.scalar("workflow_name"),
            status=status,
            spec_version=row.scalar("spec_version", int, 1),
            inputs=row.mapping("inputs"),
            intent=row.scalar("intent"),
        )
        values["origin"] = api.RunOrigin.from_dict(row.raw("origin") or {})
        values["parent_run_id"] = row.raw("parent_run_id")
        values["root_run_id"] = row.scalar("root_run_id")
        for name in ("spawned_by_node_id", "branch_key", "forked_from"):
            values[name] = row.raw(name)
        values["project_id"], values["mode"] = row.scalar("project_id"), row.scalar(
            "mode", default="background"
        )
        values["budget"] = api.RunBudget.from_dict(row.raw("budget") or {})
        values["pinned"], values["created_at"] = bool(
            row.raw("pinned", False)
        ), row.scalar("created_at")
        for name in ("started_at", "completed_at"):
            values[name] = row.raw(name)
        values["elapsed_seconds"] = row.scalar("elapsed_seconds", float, 0.0)
        for name in ("total_tokens", "agent_count"):
            values[name] = row.scalar(name, int, 0)
        values["error_message"], values["attention"] = row.scalar(
            "error_message"
        ), row.raw("attention")
        values["policy_overrides"] = row.mapping("policy_overrides")
        values["owner_username"], values["origin_harness"] = row.scalar(
            "owner_username"
        ), row.scalar("origin_harness")
        values["extra"] = row.extras(factory._KNOWN)
        return factory(**values)

    @staticmethod
    def instance(factory, payload):
        api, row = contract(), Fields(payload or {})
        state = row.enum("state", api.InstanceState, "pending")
        failure = row.raw("failure")
        values = dict(
            path=row.scalar("path"),
            state=state,
            epoch=row.scalar("epoch", int, 0),
            attempt=row.scalar("attempt", int, 0),
            declined_edges=row.strings("declined_edges"),
            degraded_reason=row.scalar("degraded_reason"),
        )
        values["failure"] = (
            api.Failure.from_dict(failure) if isinstance(failure, dict) else None
        )
        for name in ("started_at", "completed_at"):
            values[name] = row.raw(name)
        values.update(
            output_ref=row.scalar("output_ref"),
            tokens=row.scalar("tokens", int, 0),
            wake_at=row.scalar("wake_at", float, 0.0),
            item_label=row.scalar("item_label"),
            item_total=row.scalar("item_total", int, 0),
            subagent_id=row.scalar("subagent_id"),
        )
        return factory(**values)


class ModelPolicy:
    @staticmethod
    def initialize_run(run):
        if run.root_run_id:
            return
        run.root_run_id = run.id

    @staticmethod
    def lane(kind):
        api = contract()
        if kind in api.LLM_KINDS or kind is api.NodeKind.VISUALIZE:
            return api.LANE_LLM
        lanes = {
            api.NodeKind.ACTION: api.LANE_IO,
            api.NodeKind.SUBWORKFLOW: api.LANE_IO,
        }
        return lanes.get(kind, api.LANE_COMPUTE)

    @staticmethod
    def choice(value, options, fallback):
        normalized = str(value or "").strip().lower()
        return normalized if normalized in options else fallback

    @staticmethod
    def non_negative(value):
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return number if number > 0 else 0

    @staticmethod
    def belongs(run, username):
        requested = (username or "").strip().lower()
        if not requested:
            return True
        recorded = (run.owner_username or "").strip().lower()
        return recorded in ("", requested)
