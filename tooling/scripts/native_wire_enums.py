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
    if any(match.group().startswith("/*") and "/*" in match.group()[2:] for match in tokens):
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
                policy = re.fullmatch(r'rename_all\s*=\s*"(snake_case|kebab-case)"', serde[0].strip())
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
    python_files: list[Path], native_files: list[Path],
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
    evidence = {}
    for path in python_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef) or cls.name.startswith("_"):
                continue
            if not any(
                (isinstance(base, ast.Name) and base.id in {"Enum", "StrEnum"})
                or (isinstance(base, ast.Attribute) and base.attr in {"Enum", "StrEnum"})
                for base in cls.bases
            ):
                continue
            members = {}
            supported = True
            for item in cls.body:
                if not isinstance(item, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                if all(isinstance(target, ast.Name) and target.id.startswith("_") for target in targets):
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
                native_path for name, native_values, native_path in contracts
                if (
                    (alias is None and name == cls.name)
                    or (alias is not None and (native_path, name) == alias)
                )
                and (
                    native_values == frozenset(values)
                    or _member_name_transport_proof(tree, cls.name, members, native_path, native_values)
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
        ("runtime/gideon/hypermid/authz.py", "Operation",
         "crates/hypermid-core/src/capability.rs", "CapabilityOperation"),
        ("runtime/gideon/hypermid/contracts.py", "PredicateComparison",
         "crates/hypermid-memory/src/smart_note.rs", "Comparison"),
        ("runtime/gideon/hypermid/portability.py", "ContextPortabilityEntryKind",
         "crates/hypermid-context/src/export.rs", "PortabilityEntryKind"),
    )
    return {
        ((root / python).resolve(), python_name): ((root / native).resolve(), native_name)
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
            (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == returned.id)
            or any(isinstance(argument, ast.Name) and argument.id == returned.id for argument in [*node.args, *(keyword.value for keyword in node.keywords)])
        ):
            return None
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == returned.id:
                assignments.append(node)
            if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == returned.id and isinstance(target.slice, ast.Constant) and target.slice.value == field:
                return None
    if len(assignments) != 1 or assignments[0] not in method.body:
        return None
    value = assignments[0].value
    return value if isinstance(value, ast.Dict) else None


def _python_member_name_requests(tree: ast.Module, enum_name: str) -> list[tuple[str, str]]:
    requests = []
    for record in tree.body:
        if not isinstance(record, ast.ClassDef) or record.name.startswith("_"):
            continue
        methods = {node.name: node for node in record.body if isinstance(node, ast.FunctionDef)}
        encoder, decoder = methods.get("to_wire"), methods.get("from_wire")
        if encoder is None or decoder is None or len(decoder.args.args) < 2:
            continue
        if not any(isinstance(node, ast.Name) and node.id == "classmethod" for node in decoder.decorator_list):
            continue
        for declared in record.body:
            if not isinstance(declared, ast.AnnAssign) or not isinstance(declared.target, ast.Name) or not isinstance(declared.annotation, ast.Name) or declared.annotation.id != enum_name:
                continue
            field = declared.target.id
            document = _returned_wire_dict(encoder, field)
            if document is None:
                continue
            emitted = [value for key, value in zip(document.keys, document.values) if isinstance(key, ast.Constant) and key.value == field]
            expected = ast.parse(f"self.{field}.name.lower()", mode="eval").body
            if len(emitted) != 1 or ast.dump(emitted[0]) != ast.dump(expected):
                continue
            bodies = list(decoder.body)
            bodies.extend(node for block in decoder.body if isinstance(block, ast.Try) for node in block.body)
            returned = [node.value for node in bodies if isinstance(node, ast.Return)]
            all_returns = [node for node in ast.walk(decoder) if isinstance(node, ast.Return)]
            if len(returned) != 1 or len(all_returns) != 1 or not isinstance(returned[0], ast.Call):
                continue
            call = returned[0]
            if not isinstance(call.func, ast.Name) or call.func.id != decoder.args.args[0].arg:
                continue
            values = [keyword.value for keyword in call.keywords if keyword.arg == field]
            if len(values) != 1 or not isinstance(values[0], ast.Subscript) or not isinstance(values[0].value, ast.Name) or values[0].value.id != enum_name:
                continue
            lookup = values[0].slice
            if not isinstance(lookup, ast.Call) or lookup.args or lookup.keywords or not isinstance(lookup.func, ast.Attribute) or lookup.func.attr != "upper":
                continue
            source = lookup.func.value
            if not isinstance(source, ast.Subscript) or not isinstance(source.value, ast.Name) or not isinstance(source.slice, ast.Constant) or source.slice.value != field:
                continue
            if any(
                isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                and any(isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == source.value.id for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))
                for node in ast.walk(decoder)
            ):
                continue
            raw_assignments = [node for node in ast.walk(decoder) if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and any(isinstance(target, ast.Name) and target.id == source.value.id for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
            if len(raw_assignments) != 1 or raw_assignments[0] not in decoder.body:
                continue
            if any(
                isinstance(node, ast.Call)
                and (
                    (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == source.value.id and node.func.attr != "get")
                    or (any(isinstance(argument, ast.Name) and argument.id == source.value.id for argument in [*node.args, *(keyword.value for keyword in node.keywords)]) and not (isinstance(node.func, ast.Name) and node.func.id == "set"))
                )
                for node in ast.walk(decoder)
            ):
                continue
            read = raw_assignments[0].value
            if not isinstance(read, ast.Call) or not isinstance(read.func, ast.Name) or read.func.id != "_mapping" or not read.args or not isinstance(read.args[0], ast.Name) or read.args[0].id != decoder.args.args[1].arg:
                continue
            requests.append((record.name, field))
    return requests


def _member_name_transport_proof(
    tree: ast.Module, enum_name: str, members: dict[str, str], native_path: Path,
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
    declarations = [match for match in _ENUM.finditer(source) if match[1] == enum_name and not any(start <= match.start() < end for start, end in strings)]
    if len(declarations) != 1:
        return False
    declaration = declarations[0]
    attributes = declaration.group()[:declaration.group().index("pub")]
    serde = re.findall(r"#\[serde\(([^)]+)\)\]", attributes)
    if len(serde) != 1 or serde[0].strip() != 'rename_all = "snake_case"':
        return False
    variants = [item.strip() for item in declaration[2].split(",") if item.strip()]
    if not all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", item) for item in variants):
        return False
    native_members = {re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper(): re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() for name in variants}
    if len(native_members) != len(variants) or native_members != {name: name.lower() for name in members}:
        return False
    method = re.compile(
        rf"impl\s+{re.escape(enum_name)}\s*\{{\s*pub\s+const\s+fn\s+wire_name\(self\)\s*->\s*&'static\s+str\s*\{{\s*match\s+self\s*\{{"
        r'((?:\s*Self::[A-Za-z][A-Za-z0-9_]*\s*=>\s*"[^"\\]+"\s*,)+)\s*\}\s*\}'
    )
    matches = [match for match in method.finditer(source) if not any(start <= match.start() < end for start, end in strings)]
    if len(matches) != 1:
        return False
    arms = re.findall(r'Self::([A-Za-z][A-Za-z0-9_]*)\s*=>\s*"([^"\\]+)"', matches[0][1])
    wire_names = {re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper(): value for name, value in arms}
    if len(wire_names) != len(arms) or wire_names != members:
        return False
    for record, field in requests:
        struct = re.compile(_ATTRIBUTES + rf"pub\s+struct\s+{re.escape(record)}\s*\{{([^{{}}]*)\}}")
        for match in struct.finditer(source):
            if any(start <= match.start() < end for start, end in strings):
                continue
            attributes = match.group()[:match.group().index("pub")]
            derives = re.findall(r"#\[derive\(([^)]+)\)\]", attributes)
            traits = {trait.strip() for group in derives for trait in group.split(",")}
            if not {"Deserialize", "Serialize"} <= traits:
                continue
            serde = re.findall(r"#\[serde\(([^)]+)\)\]", attributes)
            if any(item.strip() != "deny_unknown_fields" for item in serde) or re.search(r"#\[(?!derive\(|serde\()", attributes):
                continue
            if re.search(rf"(?:^|,)\s*pub\s+{re.escape(field)}\s*:\s*{re.escape(enum_name)}\s*,", match[1]):
                return True
    return False
