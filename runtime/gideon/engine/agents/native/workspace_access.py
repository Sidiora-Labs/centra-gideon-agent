"""Workspace snapshots, traversal and child-process output collection."""

from __future__ import annotations

import ast
import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from gideon.core import cancellation
from gideon.engine.agents.native import read_gate


@dataclass(frozen=True)
class WorkspaceDefaults:
    directory: Path
    extra_roots: tuple[Path, ...]
    agent: str
    session: str

    @staticmethod
    def resolve(directory: Path, roots: list[Path], requested: str) -> Path:
        base = directory.resolve()
        target = (base / requested).resolve()
        for root in (base, *roots):
            if target.is_relative_to(root):
                return target
        raise ValueError(f"path {requested!r} escapes the workspace root")


@dataclass(frozen=True)
class FileSnapshot:
    text: str
    digest: str
    complete: bool
    binary: bool = False

    @classmethod
    def load(cls, path: Path, limit: int) -> FileSnapshot | None:
        if not path.is_file():
            return None
        data = path.read_bytes()
        visible = data[:limit]
        if b"\x00" in visible[:8192]:
            return cls("", "", False, True)
        return cls(
            visible.decode("utf-8", "replace"),
            read_gate.sha256_bytes(data),
            len(data) <= limit,
        )


@dataclass(frozen=True)
class TextChange:
    content: str = ""
    replacements: int = 0
    error: str = ""

    @classmethod
    def prepare(
        cls, text: str, before: str, after: str, all_matches: bool
    ) -> TextChange:
        if not before:
            return cls(error="old_str is empty")
        if before == after:
            return cls(error="old_str and new_str are identical (no change)")
        occurrences = text.count(before)
        if occurrences == 0:
            return cls(error="old_str not found in file")
        if occurrences > 1 and not all_matches:
            return cls(error=f"old_str matched {occurrences} times (not unique)")
        count = occurrences if all_matches else 1
        return cls(text.replace(before, after, count), count)


_SOURCE_SUFFIXES = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".kt",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".php",
        ".swift",
        ".scala",
        ".sh",
    }
)
_SYMBOL_LINE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:(?:function|class|interface|type|enum|struct|trait|impl|func|fn|def)\s+([A-Za-z_][\w]*)"
    r"|(?:const|let|var)\s+([A-Za-z_][\w]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_][\w]*)\s*=>)"
)


def _python_symbols(source: str) -> list[str]:
    try:
        nodes = ast.parse(source).body
    except SyntaxError:
        return []
    symbols = []
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(f"def {node.name}()")
        elif isinstance(node, ast.ClassDef):
            names = [
                method.name
                for method in node.body
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
            ][:8]
            suffix = f" ({', '.join(names)})" if names else ""
            symbols.append(f"class {node.name}{suffix}")
    return symbols[:30]


def _source_symbols(path: Path, source: str) -> list[str]:
    if path.suffix == ".py":
        return _python_symbols(source)
    found = []
    for line in source.splitlines():
        match = _SYMBOL_LINE.match(line)
        if match and (name := match.group(1) or match.group(2)):
            found.append(name)
            if len(found) == 30:
                break
    return found


@dataclass(frozen=True)
class WorkspaceTree:
    root: Path
    ignored: frozenset[str]

    @staticmethod
    def directory(path: Path) -> str | None:
        if not path.is_dir():
            return None
        names = [item.name + ("/" if item.is_dir() else "") for item in path.iterdir()]
        return "\n".join(sorted(names)) or "(empty)"

    def matching_files(self, pattern: str):
        return (path for path in self.root.glob(pattern) if path.is_file())

    def glob_listing(self, pattern: str) -> str:
        paths = sorted(
            str(path.relative_to(self.root)) for path in self.matching_files(pattern)
        )
        rows = paths[:500]
        if len(paths) > 500:
            rows += [
                f"…[showing 500 of {len(paths)} matches — narrow the pattern to see the rest]"
            ]
        return "\n".join(rows) or "(no matches)"

    def search(self, query: str, pattern: str, limit: int, regex) -> str:
        rows = []
        for path in self.matching_files(pattern):
            relative = path.relative_to(self.root)
            if self.ignored.intersection(relative.parts):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
                for number, line in enumerate(lines, 1):
                    matched = regex.search(line) if regex else query in line
                    if not matched:
                        continue
                    rows.append(f"{relative}:{number}: {line.strip()[:200]}")
                    if len(rows) >= limit:
                        rows.append(
                            f"…[stopped at max_results={limit} — more matches may exist; "
                            "narrow `glob` or raise `max_results`]"
                        )
                        return "\n".join(rows)
            except (OSError, UnicodeDecodeError):
                continue
        return "\n".join(rows) or "(no matches)"

    def source_paths(self):
        for directory, subdirs, files in self.root.walk():
            subdirs[:] = sorted(set(subdirs).difference(self.ignored))
            for name in sorted(files):
                if Path(name).suffix in _SOURCE_SUFFIXES:
                    yield directory / name

    def outline(self, limit: int) -> str:
        sources = []
        limited = False
        for path in self.source_paths():
            sources.append(path)
            if len(sources) >= limit:
                limited = True
                break
        if not sources:
            return "(no source files found under this path)"
        qualifier = (
            f", capped at max_files={limit} — map is PARTIAL; pass a "
            "subdir `path` or a higher `max_files` to see the rest"
            if limited
            else ""
        )
        rows = [
            f"# Repo map — {self.root.name}/  ({len(sources)} source files{qualifier})",
            "",
        ]
        for path in sources:
            try:
                source = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            symbols = _source_symbols(path, source)
            suffix = "\n    " + " · ".join(symbols) if symbols else ""
            rows.append(f"{path.relative_to(self.root)}{suffix}")
        return "\n".join(rows)


@dataclass(frozen=True)
class ShellCapture:
    output: str
    returncode: int | None

    @classmethod
    async def collect(cls, process, timeout: float) -> ShellCapture:
        with cancellation.track_child(process):
            try:
                output, _ = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                await cancellation.terminate_and_reap(process)
                raise
        return cls((output or b"").decode("utf-8", "replace"), process.returncode)
