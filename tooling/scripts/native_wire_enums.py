"""Exact parity evidence for Python mirrors of native serde wire enums."""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ATTRIBUTES = r"(?:#\[[^\]]+\]\s*)+"
_ENUM = re.compile(_ATTRIBUTES + r"pub\s+enum\s+(\w+)\s*\{([^{}]*)\}")
_COMMENT_OR_STRING = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', re.DOTALL)


def _native_source(path: Path) -> tuple[str, list[tuple[int, int]]] | None:
    source = path.read_text(encoding="utf-8")
    tokens = list(_COMMENT_OR_STRING.finditer(source))
    if any(
        match.group().startswith("/*") and "/*" in match.group()[2:] for match in tokens
    ):
        return None
    strings = [(match.start(), match.end()) for match in tokens if match.group(1)]
    source = _COMMENT_OR_STRING.sub(
        lambda match: match.group(1) or " " * len(match.group()), source
    )
    return source, strings


def _rust_contracts(files: list[Path]) -> list[tuple[str, frozenset[str], Path]]:
    contracts = []
    for path in files:
        cleaned = _native_source(path)
        if cleaned is None:
            continue
        source, strings = cleaned
        for match in _ENUM.finditer(source):
            if any(start <= match.start() < end for start, end in strings):
                continue
            declaration = match.group()
            attributes = declaration[: declaration.index("pub")]
            derives = re.findall(r"#\[derive\(([^)]+)\)\]", attributes)
            traits = {trait.strip() for group in derives for trait in group.split(",")}
            if not {"Deserialize", "Serialize"} <= traits:
                continue
            serde = re.findall(r"#\[serde\(([^)]+)\)\]", attributes)
            rename_all = None
            if serde:
                if len(serde) != 1:
                    continue
                policy = re.fullmatch(
                    r'rename_all\s*=\s*"(snake_case|kebab-case)"', serde[0].strip()
                )
                if policy is None:
                    continue
                rename_all = policy[1]
            if re.search(r"#\[(?!derive\(|serde\()", attributes):
                continue
            variants = [item.strip() for item in match[2].split(",") if item.strip()]
            values = []
            supported = bool(variants)
            for variant in variants:
                parsed = re.fullmatch(
                    r'(?:#\[serde\(rename\s*=\s*"([^"\\]+)"\)\]\s*)?([A-Za-z][A-Za-z0-9_]*)',
                    variant,
                )
                if parsed is None:
                    supported = False
                    break
                renamed, name = parsed.groups()
                if renamed is not None:
                    values.append(renamed)
                elif rename_all is not None:
                    separator = "_" if rename_all == "snake_case" else "-"
                    values.append(re.sub(r"(?<!^)(?=[A-Z])", separator, name).lower())
                else:
                    supported = False
                    break
            if not supported or len(set(values)) != len(values):
                continue
            wire_values = frozenset(values)
            contracts.append((match[1], wire_values, path.resolve()))
    return contracts


def native_wire_enum_evidence(
    python_files: list[Path],
    native_files: list[Path],
    aliases: dict[tuple[Path, str], tuple[Path, str]] | None = None,
) -> dict[tuple[Path, str], tuple[Path, ...]]:
    """Mirror classes with complete value parity across a proven native wire contract.

    Value construction alone is not evidence. A match requires a public native wire
    declaration that both serializes and deserializes the identical entire value set.
    Explicit source-bound aliases still require complete value parity. Nonliteral Python
    variants, private classes and unsupported serde forms stay unqualified. A request
    encoded by member name additionally requires its actual writer/reader, native DTO,
    complete serde member pairs and a complete native envelope-value map.
    """
    contracts = _rust_contracts(native_files)
    aliases = aliases or {}
    evidence = _authored_log_wire_evidence(python_files, native_files)
    for path in python_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef) or cls.name.startswith("_"):
                continue
            if not any(
                (isinstance(base, ast.Name) and base.id in {"Enum", "StrEnum"})
                or (
                    isinstance(base, ast.Attribute) and base.attr in {"Enum", "StrEnum"}
                )
                for base in cls.bases
            ):
                continue
            members = {}
            supported = True
            for item in cls.body:
                if not isinstance(item, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = (
                    item.targets if isinstance(item, ast.Assign) else [item.target]
                )
                if all(
                    isinstance(target, ast.Name) and target.id.startswith("_")
                    for target in targets
                ):
                    continue
                if (
                    len(targets) != 1
                    or not isinstance(targets[0], ast.Name)
                    or not isinstance(item.value, ast.Constant)
                    or not isinstance(item.value.value, str)
                ):
                    supported = False
                    break
                members[targets[0].id] = item.value.value
            values = list(members.values())
            if not supported or not values or len(set(values)) != len(values):
                continue
            alias = aliases.get((path.resolve(), cls.name))
            matches = tuple(
                native_path
                for name, native_values, native_path in contracts
                if (
                    (alias is None and name == cls.name)
                    or (alias is not None and (native_path, name) == alias)
                )
                and (
                    native_values == frozenset(values)
                    or _member_name_transport_proof(
                        tree, cls.name, members, native_path, native_values
                    )
                )
            )
            if matches:
                evidence[(path.resolve(), cls.name)] = matches
    return evidence


def native_wire_aliases(root: Path) -> dict[tuple[Path, str], tuple[Path, str]]:
    """Source identities of native wire types whose Python mirror has a different name.

    These identify counterparts; the matcher verifies both serde directions and every
    variant value afresh. A mapping cannot waive a missing or mismatched contract.
    """
    pairs = (
        (
            "runtime/gideon/hypermid/authz.py",
            "Operation",
            "crates/hypermid-core/src/capability.rs",
            "CapabilityOperation",
        ),
        (
            "runtime/gideon/hypermid/contracts.py",
            "PredicateComparison",
            "crates/hypermid-memory/src/smart_note.rs",
            "Comparison",
        ),
        (
            "runtime/gideon/hypermid/portability.py",
            "ContextPortabilityEntryKind",
            "crates/hypermid-context/src/export.rs",
            "PortabilityEntryKind",
        ),
    )
    return {
        ((root / python).resolve(), python_name): (
            (root / native).resolve(),
            native_name,
        )
        for python, python_name, native, native_name in pairs
    }


def _returned_wire_dict(method: ast.FunctionDef, field: str) -> ast.Dict | None:
    returns = [node for node in ast.walk(method) if isinstance(node, ast.Return)]
    if len(returns) != 1 or returns[0] not in method.body:
        return None
    returned = returns[0].value
    if isinstance(returned, ast.Dict):
        return returned
    if not isinstance(returned, ast.Name):
        return None
    assignments = []
    for node in ast.walk(method):
        if isinstance(node, ast.Call) and (
            (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == returned.id
            )
            or any(
                isinstance(argument, ast.Name) and argument.id == returned.id
                for argument in [
                    *node.args,
                    *(keyword.value for keyword in node.keywords),
                ]
            )
        ):
            return None
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == returned.id:
                assignments.append(node)
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == returned.id
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == field
            ):
                return None
    if len(assignments) != 1 or assignments[0] not in method.body:
        return None
    value = assignments[0].value
    return value if isinstance(value, ast.Dict) else None


def _python_member_name_requests(
    tree: ast.Module, enum_name: str
) -> list[tuple[str, str]]:
    requests = []
    for record in tree.body:
        if not isinstance(record, ast.ClassDef) or record.name.startswith("_"):
            continue
        methods = {
            node.name: node for node in record.body if isinstance(node, ast.FunctionDef)
        }
        encoder, decoder = methods.get("to_wire"), methods.get("from_wire")
        if encoder is None or decoder is None or len(decoder.args.args) < 2:
            continue
        if not any(
            isinstance(node, ast.Name) and node.id == "classmethod"
            for node in decoder.decorator_list
        ):
            continue
        for declared in record.body:
            if (
                not isinstance(declared, ast.AnnAssign)
                or not isinstance(declared.target, ast.Name)
                or not isinstance(declared.annotation, ast.Name)
                or declared.annotation.id != enum_name
            ):
                continue
            field = declared.target.id
            document = _returned_wire_dict(encoder, field)
            if document is None:
                continue
            emitted = [
                value
                for key, value in zip(document.keys, document.values)
                if isinstance(key, ast.Constant) and key.value == field
            ]
            expected = ast.parse(f"self.{field}.name.lower()", mode="eval").body
            if len(emitted) != 1 or ast.dump(emitted[0]) != ast.dump(expected):
                continue
            bodies = list(decoder.body)
            bodies.extend(
                node
                for block in decoder.body
                if isinstance(block, ast.Try)
                for node in block.body
            )
            returned = [node.value for node in bodies if isinstance(node, ast.Return)]
            all_returns = [
                node for node in ast.walk(decoder) if isinstance(node, ast.Return)
            ]
            if (
                len(returned) != 1
                or len(all_returns) != 1
                or not isinstance(returned[0], ast.Call)
            ):
                continue
            call = returned[0]
            if (
                not isinstance(call.func, ast.Name)
                or call.func.id != decoder.args.args[0].arg
            ):
                continue
            values = [
                keyword.value for keyword in call.keywords if keyword.arg == field
            ]
            if (
                len(values) != 1
                or not isinstance(values[0], ast.Subscript)
                or not isinstance(values[0].value, ast.Name)
                or values[0].value.id != enum_name
            ):
                continue
            lookup = values[0].slice
            if (
                not isinstance(lookup, ast.Call)
                or lookup.args
                or lookup.keywords
                or not isinstance(lookup.func, ast.Attribute)
                or lookup.func.attr != "upper"
            ):
                continue
            source = lookup.func.value
            if (
                not isinstance(source, ast.Subscript)
                or not isinstance(source.value, ast.Name)
                or not isinstance(source.slice, ast.Constant)
                or source.slice.value != field
            ):
                continue
            if any(
                isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                and any(
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == source.value.id
                    for target in (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                )
                for node in ast.walk(decoder)
            ):
                continue
            raw_assignments = [
                node
                for node in ast.walk(decoder)
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == source.value.id
                    for target in (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                )
            ]
            if len(raw_assignments) != 1 or raw_assignments[0] not in decoder.body:
                continue
            if any(
                isinstance(node, ast.Call)
                and (
                    (
                        isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == source.value.id
                        and node.func.attr != "get"
                    )
                    or (
                        any(
                            isinstance(argument, ast.Name)
                            and argument.id == source.value.id
                            for argument in [
                                *node.args,
                                *(keyword.value for keyword in node.keywords),
                            ]
                        )
                        and not (
                            isinstance(node.func, ast.Name) and node.func.id == "set"
                        )
                    )
                )
                for node in ast.walk(decoder)
            ):
                continue
            read = raw_assignments[0].value
            if (
                not isinstance(read, ast.Call)
                or not isinstance(read.func, ast.Name)
                or read.func.id != "_mapping"
                or not read.args
                or not isinstance(read.args[0], ast.Name)
                or read.args[0].id != decoder.args.args[1].arg
            ):
                continue
            requests.append((record.name, field))
    return requests


def _member_name_transport_proof(
    tree: ast.Module,
    enum_name: str,
    members: dict[str, str],
    native_path: Path,
    serde_values: frozenset[str],
) -> bool:
    """Prove both a member-name request encoding and its distinct envelope value map."""
    if frozenset(name.lower() for name in members) != serde_values:
        return False
    requests = _python_member_name_requests(tree, enum_name)
    if not requests:
        return False
    cleaned = _native_source(native_path)
    if cleaned is None:
        return False
    source, strings = cleaned
    declarations = [
        match
        for match in _ENUM.finditer(source)
        if match[1] == enum_name
        and not any(start <= match.start() < end for start, end in strings)
    ]
    if len(declarations) != 1:
        return False
    declaration = declarations[0]
    attributes = declaration.group()[: declaration.group().index("pub")]
    serde = re.findall(r"#\[serde\(([^)]+)\)\]", attributes)
    if len(serde) != 1 or serde[0].strip() != 'rename_all = "snake_case"':
        return False
    variants = [item.strip() for item in declaration[2].split(",") if item.strip()]
    if not all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", item) for item in variants):
        return False
    native_members = {
        re.sub(r"(?<!^)(?=[A-Z])", "_", name)
        .upper(): re.sub(r"(?<!^)(?=[A-Z])", "_", name)
        .lower()
        for name in variants
    }
    if len(native_members) != len(variants) or native_members != {
        name: name.lower() for name in members
    }:
        return False
    method = re.compile(
        rf"impl\s+{re.escape(enum_name)}\s*\{{\s*pub\s+const\s+fn\s+wire_name\(self\)\s*->\s*&'static\s+str\s*\{{\s*match\s+self\s*\{{"
        r'((?:\s*Self::[A-Za-z][A-Za-z0-9_]*\s*=>\s*"[^"\\]+"\s*,)+)\s*\}\s*\}'
    )
    matches = [
        match
        for match in method.finditer(source)
        if not any(start <= match.start() < end for start, end in strings)
    ]
    if len(matches) != 1:
        return False
    arms = re.findall(
        r'Self::([A-Za-z][A-Za-z0-9_]*)\s*=>\s*"([^"\\]+)"', matches[0][1]
    )
    wire_names = {
        re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper(): value for name, value in arms
    }
    if len(wire_names) != len(arms) or wire_names != members:
        return False
    for record, field in requests:
        struct = re.compile(
            _ATTRIBUTES + rf"pub\s+struct\s+{re.escape(record)}\s*\{{([^{{}}]*)\}}"
        )
        for match in struct.finditer(source):
            if any(start <= match.start() < end for start, end in strings):
                continue
            attributes = match.group()[: match.group().index("pub")]
            derives = re.findall(r"#\[derive\(([^)]+)\)\]", attributes)
            traits = {trait.strip() for group in derives for trait in group.split(",")}
            if not {"Deserialize", "Serialize"} <= traits:
                continue
            serde = re.findall(r"#\[serde\(([^)]+)\)\]", attributes)
            if any(
                item.strip() != "deny_unknown_fields" for item in serde
            ) or re.search(r"#\[(?!derive\(|serde\()", attributes):
                continue
            if re.search(
                rf"(?:^|,)\s*pub\s+{re.escape(field)}\s*:\s*{re.escape(enum_name)}\s*,",
                match[1],
            ):
                return True
    return False


def _expression_is(node: ast.AST | None, expression: str) -> bool:
    return node is not None and ast.dump(node) == ast.dump(
        ast.parse(expression, mode="eval").body
    )


def _named_definition(body: list[ast.stmt], name: str, kind: type) -> ast.AST | None:
    return next(
        (node for node in body if isinstance(node, kind) and node.name == name), None
    )


def _authored_level_parser(tree: ast.Module) -> tuple[list[str], dict[str, str]] | None:
    level = _named_definition(tree.body, "LogLevel", ast.ClassDef)
    if level is None or not any(
        isinstance(base, ast.Name) and base.id == "IntEnum" for base in level.bases
    ):
        return None
    members = []
    for node in level.body:
        if not isinstance(node, ast.Assign):
            continue
        if (
            len(node.targets) != 1
            or not isinstance(node.targets[0], ast.Name)
            or not isinstance(node.value, ast.Constant)
            or type(node.value.value) is not int
            or node.value.value != len(members)
        ):
            return None
        members.append(node.targets[0].id)
    parser = _named_definition(level.body, "parse", ast.FunctionDef)
    if (
        not members
        or parser is None
        or len(parser.args.args) != 2
        or not any(
            isinstance(node, ast.Name) and node.id == "classmethod"
            for node in parser.decorator_list
        )
    ):
        return None
    if len(parser.body) < 2 or not isinstance(parser.body[0], ast.Assign):
        return None
    normalization = parser.body[0]
    if (
        len(normalization.targets) != 1
        or not isinstance(normalization.targets[0], ast.Name)
        or not _expression_is(
            normalization.value, f"{parser.args.args[1].arg}.strip().upper()"
        )
    ):
        return None
    variable = normalization.targets[0].id
    accepted = {name.lower(): name.lower() for name in members}
    for branch in parser.body[1:-1]:
        if (
            not isinstance(branch, ast.If)
            or branch.orelse
            or len(branch.body) != 1
            or not isinstance(branch.test, ast.Compare)
            or not _expression_is(branch.test.left, variable)
            or len(branch.test.ops) != 1
            or not isinstance(branch.test.ops[0], ast.Eq)
            or len(branch.test.comparators) != 1
        ):
            return None
        alias = branch.test.comparators[0]
        assignment = branch.body[0]
        if (
            not isinstance(alias, ast.Constant)
            or not isinstance(alias.value, str)
            or not isinstance(assignment, ast.Assign)
            or len(assignment.targets) != 1
            or not isinstance(assignment.targets[0], ast.Name)
            or assignment.targets[0].id != variable
            or not isinstance(assignment.value, ast.Constant)
            or assignment.value.value not in members
        ):
            return None
        if (
            alias.value != alias.value.upper()
            or alias.value in members
            or alias.value.lower() in accepted
        ):
            return None
        accepted[alias.value.lower()] = assignment.value.value.lower()
    lookup = parser.body[-1]
    if (
        not isinstance(lookup, ast.Try)
        or lookup.orelse
        or lookup.finalbody
        or len(lookup.handlers) != 1
        or len(lookup.body) != 1
        or not isinstance(lookup.body[0], ast.Return)
        or not _expression_is(
            lookup.body[0].value, f"{parser.args.args[0].arg}[{variable}]"
        )
    ):
        return None
    if not any(
        isinstance(handler.type, ast.Name)
        and handler.type.id == "KeyError"
        and len(handler.body) == 1
        and isinstance(handler.body[0], ast.Raise)
        and isinstance(handler.body[0].exc, ast.Call)
        and isinstance(handler.body[0].exc.func, ast.Name)
        and handler.body[0].exc.func.id == "ValueError"
        for handler in lookup.handlers
    ):
        return None
    return members, accepted


def _log_json_roundtrip(tree: ast.Module) -> bool:
    record = _named_definition(tree.body, "LogRecord", ast.ClassDef)
    formatter = _named_definition(tree.body, "format_line", ast.FunctionDef)
    reader = _named_definition(tree.body, "parse_line", ast.FunctionDef)
    redactor = _named_definition(tree.body, "redact_record", ast.FunctionDef)
    if any(node is None for node in (record, formatter, reader, redactor)):
        return False
    if not any(
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "level"
        and _expression_is(node.annotation, "LogLevel")
        for node in record.body
    ):
        return False
    returns = [node for node in formatter.body if isinstance(node, ast.Return)]
    if len(returns) != 1:
        return False
    encoded = returns[0].value
    if (
        not isinstance(encoded, ast.Call)
        or encoded.args
        or encoded.keywords
        or not isinstance(encoded.func, ast.Attribute)
        or encoded.func.attr != "encode"
    ):
        return False
    joined = encoded.func.value
    if (
        not isinstance(joined, ast.BinOp)
        or not isinstance(joined.op, ast.Add)
        or not _expression_is(joined.right, repr("\n"))
        or not isinstance(joined.left, ast.Call)
        or not _expression_is(joined.left.func, "json.dumps")
        or not joined.left.args
        or not isinstance(joined.left.args[0], ast.Name)
    ):
        return False
    document_name = joined.left.args[0].id
    # Reuse the returned-dictionary guard after projecting the final JSON codec out.
    import copy

    projected = copy.deepcopy(formatter)
    next(node for node in projected.body if isinstance(node, ast.Return)).value = (
        ast.Name(id=document_name, ctx=ast.Load())
    )
    document = _returned_wire_dict(projected, "level")
    if document is None:
        return False
    emitted = [
        value
        for key, value in zip(document.keys, document.values)
        if isinstance(key, ast.Constant) and key.value == "level"
    ]
    if len(emitted) != 1 or not _expression_is(
        emitted[0], f"{formatter.args.args[0].arg}.level.name.lower()"
    ):
        return False
    loads = [
        node
        for node in ast.walk(reader)
        if isinstance(node, ast.Assign)
        and _expression_is(node.value, f"json.loads({reader.args.args[0].arg})")
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]
    if len(loads) != 1:
        return False
    raw_name = loads[0].targets[0].id
    raw_writes = [
        node
        for node in ast.walk(reader)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
        and any(
            (isinstance(target, ast.Name) and target.id == raw_name)
            or (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == raw_name
            )
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
    ]
    if len(raw_writes) != 1:
        return False
    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == raw_name
        and node.func.attr != "get"
        for node in ast.walk(reader)
    ):
        return False
    decoded = [
        node
        for node in ast.walk(reader)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and _expression_is(node.value.func, "LogRecord")
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]
    if len(decoded) != 1:
        return False
    levels = [
        keyword.value for keyword in decoded[0].value.keywords if keyword.arg == "level"
    ]
    if len(levels) != 1 or not _expression_is(
        levels[0], f'LogLevel.parse(str({raw_name}["level"]))'
    ):
        return False
    if not any(
        isinstance(node, ast.Return)
        and _expression_is(node.value, decoded[0].targets[0].id)
        for node in ast.walk(reader)
    ):
        return False
    return any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Call)
        and _expression_is(node.value.func, "LogRecord")
        and any(
            keyword.arg == "level"
            and _expression_is(keyword.value, f"{redactor.args.args[0].arg}.level")
            for keyword in node.value.keywords
        )
        for node in redactor.body
    )


def _log_writer_consumes_authored_level(tree: ast.Module) -> bool:
    service = _named_definition(tree.body, "FoundationService", ast.ClassDef)
    method = (
        _named_definition(service.body, "emit_trace", ast.FunctionDef)
        if service
        else None
    )
    if method is None or not any(
        argument.arg == "level" and _expression_is(argument.annotation, "LogLevel")
        for argument in method.args.kwonlyargs
    ):
        return False
    if not any(
        isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "observability"
        and {"LogLevel", "LogRecord", "redact_record", "format_line"}
        <= {alias.name for alias in node.names if alias.asname is None}
        for node in tree.body
    ):
        return False
    records = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and _expression_is(node.value.func, "redact_record")
        and node.value.args
        and isinstance(node.value.args[0], ast.Call)
        and _expression_is(node.value.args[0].func, "LogRecord")
        and any(
            keyword.arg == "level" and _expression_is(keyword.value, "level")
            for keyword in node.value.args[0].keywords
        )
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]
    if len(records) != 1:
        return False
    name = records[0].targets[0].id
    record_writes = [
        node
        for node in ast.walk(method)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
        and any(
            (isinstance(target, ast.Name) and target.id == name)
            or (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == name
            )
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
    ]
    if len(record_writes) != 1:
        return False
    if any(
        isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "level"
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
        for node in ast.walk(method)
    ):
        return False
    return any(
        isinstance(node, ast.Call)
        and _expression_is(node.func, "self._log_writer.append")
        and len(node.args) == 2
        and _expression_is(node.args[1], f"format_line({name})")
        for node in ast.walk(method)
    )


def _authored_log_wire_evidence(
    python_files: list[Path],
    native_files: list[Path],
) -> dict[tuple[Path, str], tuple[Path, ...]]:
    """The operational log-level wire boundary, rather than a dynamic lookup exemption."""
    evidence = {}
    python_set = {path.resolve() for path in python_files}
    native_set = {path.resolve() for path in native_files}
    for path in python_files:
        if path.parts[-4:] != ("runtime", "gideon", "hypermid", "observability.py"):
            continue
        root = path.resolve().parents[3]
        writer = root / "runtime/gideon/hypermid/foundation_service.py"
        filter_path = root / "crates/hypermid-log/src/filter.rs"
        format_path = root / "crates/hypermid-log/src/format.rs"
        if writer not in python_set or not {filter_path, format_path} <= native_set:
            continue
        tree = ast.parse(path.read_text())
        parsed = _authored_level_parser(tree)
        if (
            parsed is None
            or not _log_json_roundtrip(tree)
            or not _log_writer_consumes_authored_level(ast.parse(writer.read_text()))
        ):
            continue
        cleaned = _native_source(filter_path)
        formatted = _native_source(format_path)
        if cleaned is None or formatted is None:
            continue
        source, strings = cleaned
        declarations = [
            match
            for match in _ENUM.finditer(source)
            if match[1] == "Level"
            and not any(start <= match.start() < end for start, end in strings)
        ]
        if len(declarations) != 1:
            continue
        declaration = declarations[0]
        attributes = declaration.group()[: declaration.group().index("pub")]
        derives = re.findall(r"#\[derive\(([^)]+)\)\]", attributes)
        traits = {trait.strip() for group in derives for trait in group.split(",")}
        serde = re.findall(r"#\[serde\(([^)]+)\)\]", attributes)
        if (
            not {"Deserialize", "Serialize"} <= traits
            or serde != ['rename_all = "lowercase"']
            or re.search(r"#\[(?!derive\(|serde\()", attributes)
        ):
            continue
        variants = [item.strip() for item in declaration[2].split(",") if item.strip()]
        if (
            not all(
                re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", variant) for variant in variants
            )
            or [variant.upper() for variant in variants] != parsed[0]
        ):
            continue
        from_str = re.search(
            r"impl\s+FromStr\s+for\s+Level\s*\{\s*type\s+Err\s*=\s*\w+;\s*fn\s+from_str\(value:\s*&str\)\s*->\s*Result<Self,\s*Self::Err>\s*\{\s*match\s+value\.trim\(\)\.to_ascii_lowercase\(\)\.as_str\(\)\s*\{([^{}]*)\}",
            source,
        )
        if from_str is None or any(
            start <= from_str.start() < end for start, end in strings
        ):
            continue
        arm_pattern = re.compile(
            r'((?:"[^"\\]+"\s*\|\s*)*"[^"\\]+")\s*=>\s*Ok\(Self::(\w+)\)\s*,'
        )
        arms = list(arm_pattern.finditer(from_str[1]))
        pairs = [
            (value, arm[2].lower())
            for arm in arms
            for value in re.findall(r'"([^"\\]+)"', arm[1])
        ]
        accepted = dict(pairs)
        if len(accepted) != len(pairs):
            continue
        rest = arm_pattern.sub("", from_str[1]).strip()
        if (
            accepted != parsed[1]
            or re.fullmatch(r"_\s*=>\s*Err\(\w+::\w+\)\s*,?", rest) is None
        ):
            continue
        record_source, record_strings = formatted
        records = list(
            re.finditer(
                _ATTRIBUTES + r"pub\s+struct\s+LogRecord\s*\{([^{}]*)\}", record_source
            )
        )
        if len(records) != 1 or any(
            start <= records[0].start() < end for start, end in record_strings
        ):
            continue
        record = records[0]
        record_attrs = record.group()[: record.group().index("pub")]
        record_traits = {
            trait.strip()
            for group in re.findall(r"#\[derive\(([^)]+)\)\]", record_attrs)
            for trait in group.split(",")
        }
        if (
            not {"Deserialize", "Serialize"} <= record_traits
            or re.search(r"#\[(?!derive\(|serde\()", record_attrs)
            or re.findall(r"#\[serde\(([^)]+)\)\]", record_attrs)
            != ["deny_unknown_fields"]
        ):
            continue
        if (
            re.search(r"(?:^|,)\s*pub\s+level\s*:\s*Level\s*,", record[1]) is None
            or re.search(r"use\s+crate::filter::\{[^}]*\bLevel\b[^}]*\}", record_source)
            is None
        ):
            continue
        evidence[(path.resolve(), "LogLevel")] = (filter_path, format_path)
    return evidence
