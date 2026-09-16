from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_TREE_DEPTH = 2

MAX_README_CHARS = 8_000

MAX_TREE_ENTRIES = 120

DEFAULT_TTL_SECS = 7 * 24 * 3600

_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".idea",
        ".vscode",
        "target",
        ".next",
        ".cache",
        "coverage",
        ".tox",
        "site-packages",
    }
)

_README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md")

_METADATA_MARKERS: tuple[tuple[str, str], ...] = (
    ("pyproject.toml", "Python (pyproject.toml)"),
    ("setup.py", "Python (setup.py)"),
    ("requirements.txt", "Python (requirements.txt)"),
    ("package.json", "Node/JS (package.json)"),
    ("tsconfig.json", "TypeScript (tsconfig.json)"),
    ("Cargo.toml", "Rust (Cargo.toml)"),
    ("go.mod", "Go (go.mod)"),
    ("pom.xml", "Java/Maven (pom.xml)"),
    ("build.gradle", "Java/Gradle (build.gradle)"),
    ("Gemfile", "Ruby (Gemfile)"),
    ("Makefile", "Makefile present"),
    ("Dockerfile", "Dockerfile present"),
    ("docker-compose.yml", "docker-compose present"),
)


@dataclass
class CodebaseContext:
    tree: list[str] = field(default_factory=list)
    tree_truncated: int = 0
    readme_head: str = ""
    metadata: list[str] = field(default_factory=list)

    def render(self) -> str:
        blocks = []
        if self.metadata:
            blocks += ["Project type: " + ", ".join(self.metadata)]
        if self.tree:
            rows = [f"- {entry}" for entry in self.tree]
            if self.tree_truncated:
                rows += [f"- (+{self.tree_truncated} more entries not listed)"]
            blocks += ["Layout (depth-limited):\n" + "\n".join(rows)]
        if self.readme_head:
            blocks += ["README (head):\n" + self.readme_head]
        return "\n\n".join(blocks)


def tree_hash(entries: list[str], truncated: int) -> str:
    payload = (
        "\n".join(entries).encode("utf-8", "replace"),
        b"\x00",
        str(truncated).encode("ascii"),
    )
    return hashlib.sha256(b"".join(payload)).hexdigest()[:16]


class _DirectoryListing:
    def __init__(self, base: Path, max_depth: int) -> None:
        self.base, self.max_depth = base, max_depth

    @staticmethod
    def children(directory: Path):
        return iter(sorted(directory.iterdir(), key=lambda path: path.name))

    def extend(self, current: Path, depth: int, output: list[str]) -> None:
        if depth >= self.max_depth:
            return
        frames = [(self.children(current), depth)]
        while frames:
            children, level = frames[-1]
            try:
                child = next(children)
            except StopIteration:
                frames.pop()
                continue
            name = child.name
            if name.startswith(".") and name != ".github":
                continue
            directory = child.is_dir()
            if directory and name in _IGNORE_DIRS:
                continue
            relative = child.relative_to(self.base).as_posix()
            output.append(relative + ("/" if directory else ""))
            if directory and not (level + 1 >= self.max_depth):
                frames.append((self.children(child), level + 1))


def _walk(
    base: Path, current: Path, depth: int, max_depth: int, out: list[str]
) -> None:
    _DirectoryListing(base, max_depth).extend(current, depth, out)


def depth_filtered_tree(
    root: Path, *, max_depth: int = MAX_TREE_DEPTH
) -> tuple[list[str], int]:
    collected: list[str] = []
    try:
        _walk(root, root, 0, max_depth, collected)
    except OSError:
        logger.debug("brownfield tree walk failed under %s", root, exc_info=True)
        return [], 0
    ordered = sorted(collected)
    omitted = max(0, len(ordered) - MAX_TREE_ENTRIES)
    return (ordered[:MAX_TREE_ENTRIES], omitted) if omitted else (ordered, 0)


def _readme_head(root: Path, *, cap: int = MAX_README_CHARS) -> str:
    candidates = (root / name for name in _README_NAMES)
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            contents = candidate.read_text(encoding="utf-8", errors="replace")
            return contents.strip()[:cap]
        except OSError:
            logger.debug(
                "brownfield README read failed for %s", candidate, exc_info=True
            )
    return ""


def _project_metadata(root: Path) -> list[str]:
    labels = []
    for marker in _METADATA_MARKERS:
        try:
            present = (root / marker[0]).is_file()
        except OSError:
            continue
        if present:
            labels.append(marker[1])
    return labels


def _directory_ready(root: Path) -> bool:
    try:
        return root.is_dir()
    except OSError:
        return False


class _ContextReading:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.entries, self.omitted = depth_filtered_tree(root)

    def context(self) -> CodebaseContext:
        readme = _readme_head(self.root)
        metadata = _project_metadata(self.root)
        return CodebaseContext(
            tree=self.entries,
            tree_truncated=self.omitted,
            readme_head=readme,
            metadata=metadata,
        )


def build_codebase_context(root: Path) -> CodebaseContext | None:
    return _ContextReading(root).context() if _directory_ready(root) else None


class _CachedContext:
    def __init__(self, value) -> None:
        self.value = value

    def render_at(self, expected: str, ttl: int, now: float | None) -> str | None:
        if not isinstance(self.value, dict):
            return None
        if self.value.get("tree_hash") != expected:
            return None
        written = self.value.get("stored_at")
        clock = time.time() if now is None else now
        if isinstance(written, (int, float)):
            if not (clock - written > ttl):
                text = self.value.get("rendered")
                if isinstance(text, str):
                    return text
        return None


@dataclass
class BrownfieldCache:
    path: Path
    ttl_secs: int = DEFAULT_TTL_SECS

    def get(
        self, project_id: str, tree_hash: str, *, now: float | None = None
    ) -> str | None:
        row = _CachedContext(self._read().get(project_id))
        return row.render_at(tree_hash, self.ttl_secs, now)

    def put(
        self,
        project_id: str,
        tree_hash: str,
        rendered: str,
        *,
        now: float | None = None,
    ) -> None:
        records = self._read()
        records[project_id] = dict(
            tree_hash=tree_hash,
            rendered=rendered,
            stored_at=time.time() if now is None else now,
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps(records, ensure_ascii=False, sort_keys=True)
            self.path.write_text(encoded, encoding="utf-8")
        except OSError:
            logger.debug(
                "brownfield cache write failed at %s", self.path, exc_info=True
            )

    def _read(self) -> dict:
        try:
            if not self.path.is_file():
                return {}
            decoded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.debug("brownfield cache read failed at %s", self.path, exc_info=True)
            return {}
        return decoded if isinstance(decoded, dict) else {}


def codebase_context(
    project_id: str,
    root: Path,
    *,
    cache: BrownfieldCache | None = None,
    now: float | None = None,
) -> str:
    if not _directory_ready(root):
        return ""
    reading = _ContextReading(root)
    identity = tree_hash(reading.entries, reading.omitted)
    if cache is not None:
        cached = cache.get(project_id, identity, now=now)
        if cached is not None:
            return cached
    rendered = reading.context().render()
    if cache is not None and project_id:
        cache.put(project_id, identity, rendered, now=now)
    return rendered
