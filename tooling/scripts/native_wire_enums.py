"""Exact parity evidence for Python mirrors of native serde wire enums."""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ATTRIBUTES = r"(?:#\[[^\]]+\]\s*)+"
_ENUM = re.compile(_ATTRIBUTES + r"pub\s+enum\s+(\w+)\s*\{([^{}]*)\}")
_COMMENT_OR_STRING = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', re.DOTALL)


def _rust_contracts(files: list[Path]) -> list[tuple[str, frozenset[str], Path]]:
    contracts = []
    for path in files:
        source = path.read_text(encoding="utf-8")
        tokens = list(_COMMENT_OR_STRING.finditer(source))
        if any(match.group().startswith("/*") and "/*" in match.group()[2:] for match in tokens):
            continue
        strings = [(match.start(), match.end()) for match in tokens if match.group(1)]
        source = _COMMENT_OR_STRING.sub(
            lambda match: match.group(1) or " " * len(match.group()), source
        )
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
    """Mirror classes with the same name and complete literal variant values.

    Value construction alone is not evidence. A match requires a public native wire
    declaration that both serializes and deserializes the identical entire value set.
    Explicit source-bound aliases still require complete value parity. Nonliteral Python
    variants, private classes and unsupported serde forms stay unqualified.
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
            values = []
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
                values.append(item.value.value)
            if not supported or not values or len(set(values)) != len(values):
                continue
            alias = aliases.get((path.resolve(), cls.name))
            matches = tuple(
                native_path for name, native_values, native_path in contracts
                if native_values == frozenset(values)
                and (
                    (alias is None and name == cls.name)
                    or (alias is not None and (native_path, name) == alias)
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
