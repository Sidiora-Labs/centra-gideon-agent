"""Markdown memory journals with a disposable full-text projection."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader
from gideon.core.sqlite_compat import FTS5_REMEDY, probe, sqlite3

if TYPE_CHECKING:
    from gideon.cognition.vector_memory import SemanticArchive

logger = logging.getLogger(__name__)
WORKSPACE_DIR_NAME = "workspace"
MEMORY_DIR_NAME = "memory"
HISTORY_DIR_NAME = "history"
PREFERENCES_FILE = "preferences.md"
PROJECTS_FILE = "projects.md"
_DEFAULT_PREFERENCES = "# User Preferences\n\n<!-- Learned from conversations -->\n"
_DEFAULT_PROJECTS = "# Active Projects\n\n<!-- Current work context -->\n"
_PROJECT_HEADING = "# Active Projects"


def config_dir() -> Path:
    return config_loader.config_dir()


def workspace_dir() -> Path:
    return config_dir().joinpath(WORKSPACE_DIR_NAME)


def memory_dir() -> Path:
    return workspace_dir().joinpath(MEMORY_DIR_NAME)


@dataclass(frozen=True)
class _JournalPage:
    location: Path
    heading: str
    initial: str

    def read(self) -> str:
        if not self.location.exists():
            return ""
        return self.location.read_text(encoding="utf-8")

    def initialize(self) -> None:
        if not self.location.exists():
            atomic_write(self.location, self.initial)

    def meaningful(self, content: str) -> bool:
        value = content.strip()
        return bool(value and value != self.initial.strip())


class MemoryJournal:
    """Own the readable preference, project, and daily-history files."""

    def __init__(self, workspace: Path | None = None):
        home = workspace or workspace_dir()
        directory = home / MEMORY_DIR_NAME
        self._pages = (
            _JournalPage(
                directory / PREFERENCES_FILE, "User Preferences", _DEFAULT_PREFERENCES
            ),
            _JournalPage(
                directory / PROJECTS_FILE, "Active Projects", _DEFAULT_PROJECTS
            ),
        )
        self._daily = directory / HISTORY_DIR_NAME
        self._index_db = (workspace or config_dir()) / "memory_index.db"
        self._archive: SemanticArchive | None = None
        self._fts_available = probe().fts5
        if not self._fts_available:
            logger.warning("Memory full-text search disabled. %s", FTS5_REMEDY)

    @property
    def name(self) -> str:
        return "native"

    @property
    def vector_store(self) -> SemanticArchive | None:
        return self._archive

    @vector_store.setter
    def vector_store(self, store: SemanticArchive | None) -> None:
        self._archive = store

    def init(self) -> None:
        self._daily.mkdir(parents=True, exist_ok=True)
        for page in self._pages:
            page.initialize()

    def _persist(self, destination: Path, content: str) -> None:
        atomic_write(destination, content)
        self._index_file(destination, content)

    def _visible_pages(self) -> Iterator[tuple[_JournalPage, str]]:
        for page in self._pages:
            content = page.read()
            if page.meaningful(content):
                yield page, content

    def read_preferences(self) -> str:
        return self._pages[0].read()

    def write_preferences(self, content: str) -> None:
        self._persist(self._pages[0].location, content)

    def add_preference(self, preference: str) -> None:
        existing = self.read_preferences()
        if preference in existing:
            return
        self.write_preferences("".join((existing, "- ", preference, "\n")))

    def read_projects(self) -> str:
        return self._pages[1].read()

    def write_projects(self, content: str) -> None:
        normalized = content.strip()
        if normalized.startswith(_PROJECT_HEADING):
            rendered = normalized + "\n"
        else:
            rendered = "\n".join(
                (
                    _PROJECT_HEADING,
                    "",
                    f"_Updated: {datetime.now():%Y-%m-%d}_",
                    "",
                    content,
                    "",
                )
            )
        self._persist(self._pages[1].location, rendered)

    def read(self) -> str:
        return "\n\n".join(content for _page, content in self._visible_pages())

    def write(self, content: str) -> None:
        preferences, separator, projects = content.partition(_PROJECT_HEADING)
        if not separator:
            self.write_preferences(content)
            return
        self.write_preferences(preferences.strip() + "\n")
        self._persist(self._pages[1].location, (separator + projects).strip() + "\n")

    def _today_history_file(self) -> Path:
        return self._daily / f"{datetime.now():%Y-%m-%d}.md"

    def append_history(self, entry: str) -> None:
        destination = self._today_history_file()
        previous = (
            destination.read_text(encoding="utf-8") if destination.exists() else ""
        )
        timestamp = datetime.now().astimezone().strftime("%H:%M %Z")
        heading = previous or f"# {datetime.now():%Y-%m-%d}\n"
        self._persist(destination, f"{heading}\n#### {timestamp}\n{entry.strip()}\n")

    def _history_files_over_retention(self, keep_days: int) -> list[Path]:
        threshold = datetime.now().date() - timedelta(days=keep_days)
        expired = []
        for candidate in self._daily.glob("*.md"):
            try:
                recorded = datetime.strptime(candidate.stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if recorded < threshold:
                expired.append(candidate)
        return expired

    def count_history_over_retention(self, keep_days: int = 365) -> int:
        return len(self._history_files_over_retention(keep_days))

    def prune_history(self, keep_days: int = 365) -> int:
        removed = 0
        for candidate in self._history_files_over_retention(keep_days):
            try:
                candidate.unlink()
            except OSError:
                logger.debug(
                    "Could not remove memory history %s", candidate, exc_info=True
                )
            else:
                removed += 1
                self._unindex_file(candidate)
        if removed:
            logger.info(
                "Removed %d daily memory files older than %d days", removed, keep_days
            )
        return removed

    @staticmethod
    def _summarize_day(content: str) -> str:
        heading, marker, remainder = content.partition("####")
        if not marker:
            return heading.strip()
        first, another, rest = remainder.partition("####")
        summary = heading.strip()
        if first.strip():
            summary += "\n#### " + first.strip()
        if another:
            summary += f"\n_…{1 + rest.count('####')} more entries_"
        return summary

    def _history_excerpt(self, days: int) -> Iterator[str]:
        current = datetime.now().date()
        for age in range(181):
            stamp = current - timedelta(days=age)
            path = self._daily / f"{stamp:%Y-%m-%d}.md"
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                continue
            if age < days:
                yield content
            elif age < 61:
                yield self._summarize_day(content)
            else:
                yield f"# {stamp:%Y-%m-%d}\n_{content.count('####')} conversation(s)_"

    def read_recent_history(self, days: int = 14) -> str:
        return "\n\n".join(self._history_excerpt(days)) if days > 0 else ""

    def read_history(self, days: int = 14) -> str:
        return self.read_recent_history(days)

    def render_markdown_context(
        self,
        prefs_cap: int = 4_000,
        projects_cap: int = 6_000,
        history_cap: int = 25_000,
    ) -> list[str]:
        limits = dict(
            zip((page.heading for page in self._pages), (prefs_cap, projects_cap))
        )
        blocks = [
            (page.heading, str(page.location), content, limits[page.heading])
            for page, content in self._visible_pages()
        ]
        history = self.read_recent_history(days=14)
        if history.strip():
            blocks.append(
                (
                    "Recent History",
                    f"{self._daily}, last 180 days decaying",
                    history,
                    history_cap,
                )
            )
        rendered = []
        for title, source, content, limit in blocks:
            clipped = (
                content[:limit] + "\n…[truncated]" if len(content) > limit else content
            )
            rendered.append(f"## {title}\n_[source: {source}]_\n{clipped}")
        return rendered

    def _try_create_db(self) -> sqlite3.Connection:
        database = sqlite3.connect(str(self._index_db))
        try:
            database.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING "
                "fts5(path, content, tokenize='porter unicode61')"
            )
        except Exception:
            database.close()
            raise
        return database

    def _get_db(self) -> sqlite3.Connection:
        for attempt in range(2):
            try:
                return self._try_create_db()
            except Exception as error:
                if attempt:
                    raise
                logger.warning("Recreating unreadable memory search index: %s", error)
                for ending in ("", "-wal", "-shm"):
                    Path(f"{self._index_db}{ending}").unlink(missing_ok=True)
        raise AssertionError("unreachable")

    @contextmanager
    def _database(self) -> Iterator[sqlite3.Connection]:
        database = self._get_db()
        try:
            with database:
                yield database
        finally:
            database.close()

    def _replace_index_entry(self, path: Path, content: str | None) -> None:
        if not self._fts_available:
            return
        try:
            with self._database() as database:
                database.execute("DELETE FROM memory_fts WHERE path = ?", (str(path),))
                if content is not None:
                    database.execute(
                        "INSERT INTO memory_fts (path, content) VALUES (?, ?)",
                        (str(path), content),
                    )
        except Exception:
            logger.debug("Memory search projection update failed", exc_info=True)

    def _index_file(self, path: Path, content: str) -> None:
        self._replace_index_entry(path, content)

    def _unindex_file(self, path: Path) -> None:
        self._replace_index_entry(path, None)

    def _indexable_files(self) -> list[tuple[str, str]]:
        paths = [page.location for page in self._pages if page.location.exists()]
        paths.extend(self._daily.glob("*.md"))
        return [(str(path), path.read_text(encoding="utf-8")) for path in paths]

    def fts_desync_count(self) -> int:
        if not self._fts_available:
            return 0
        files = dict(self._indexable_files())
        try:
            with self._database() as database:
                indexed = {
                    str(path): str(content)
                    for path, content in database.execute(
                        "SELECT path, content FROM memory_fts"
                    )
                }
        except Exception:
            logger.debug("Could not compare memory search projection", exc_info=True)
            return 0
        missing = object()
        return sum(
            files.get(path, missing) != indexed.get(path, missing)
            for path in files.keys() | indexed.keys()
        )

    def rebuild_index(self) -> int:
        if not self._fts_available:
            return 0
        files = self._indexable_files()
        try:
            with self._database() as database:
                database.execute("DELETE FROM memory_fts")
                database.executemany(
                    "INSERT INTO memory_fts (path, content) VALUES (?, ?)", files
                )
        except Exception:
            logger.warning("Could not rebuild memory search projection", exc_info=True)
        return len(files)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        if not self._fts_available:
            return []
        try:
            with self._database() as database:
                matches = database.execute(
                    "SELECT path, snippet(memory_fts, 1, '>>>', '<<<', '...', 32), rank "
                    "FROM memory_fts WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?",
                    (query, limit),
                )
                return [
                    dict(zip(("path", "snippet", "rank"), match)) for match in matches
                ]
        except Exception:
            logger.debug("Memory search unavailable", exc_info=True)
            return []
