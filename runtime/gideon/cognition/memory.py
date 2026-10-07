"""Markdown memory journals with a safely repairable full-text projection."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlite3 import Connection as SQLiteConnection

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

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
_INDEX_FAILURES: dict[str, str] = {}
_INDEX_FAILURES_LOCK = threading.Lock()
_INDEX_REPAIR_LOCK = threading.Lock()
_INDEX_SQL = "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(path, content, tokenize='porter unicode61')"


def degraded_keyword_indexes() -> dict[str, str]:
    with _INDEX_FAILURES_LOCK:
        return dict(_INDEX_FAILURES)


def _index_failure_reason(error: BaseException) -> str:
    name = str(getattr(error, "sqlite_errorname", "") or "")
    reasons = {
        "SQLITE_BUSY": "another connection holds the database locked",
        "SQLITE_LOCKED": "another connection holds the database locked",
        "SQLITE_READONLY": "the database is read-only",
        "SQLITE_FULL": "the disk is full",
        "SQLITE_CANTOPEN": "the database could not be opened",
        "SQLITE_IOERR": "the disk could not read or write the database",
        "SQLITE_PERM": "permission to the database was denied",
        "SQLITE_CORRUPT": "SQLite reported damaged database or index pages",
        "SQLITE_NOTADB": "the file is not a readable SQLite database",
    }
    for code, reason in reasons.items():
        if name == code or name.startswith(code + "_"):
            return reason
    text = str(error)
    if "no such module: fts5" in text:
        return FTS5_REMEDY
    if "locked" in text:
        return reasons["SQLITE_BUSY"]
    if "readonly" in text:
        return reasons["SQLITE_READONLY"]
    return "the keyword search projection could not be used"


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

    def _record_index_failure(self, error: BaseException) -> None:
        reason = _index_failure_reason(error)
        with _INDEX_FAILURES_LOCK:
            _INDEX_FAILURES[str(self._index_db.resolve())] = reason
        logger.warning("Memory keyword search degraded: %s", reason)

    def search_degraded(self) -> str:
        if not self._fts_available:
            return FTS5_REMEDY
        with _INDEX_FAILURES_LOCK:
            return _INDEX_FAILURES.get(str(self._index_db.resolve()), "")

    def keyword_index_status(self) -> dict:
        reason = self.search_degraded()
        if not reason:
            try:
                with self._database() as database:
                    database.execute("SELECT COUNT(*) FROM memory_fts").fetchone()
            except (sqlite3.Error, OSError) as error:
                self._record_index_failure(error)
                reason = self.search_degraded()
        if not reason:
            try:
                missing = self.fts_desync_count()
                reason = self.search_degraded()
                if missing and not reason:
                    reason = f"Keyword search differs from {missing} current memory file(s). Rebuild its index to restore coverage."
            except OSError as error:
                self._record_index_failure(error)
                reason = self.search_degraded()
        return {
            "state": "degraded" if reason else "available",
            "detail": reason
            or "Keyword search can read its index over the memory files.",
            "repair_id": (
                "memory.rebuild-fts" if reason and self._fts_available else None
            ),
        }

    def _check_database_header(self) -> None:
        # Opening malformed bytes can make SQLite discard WAL/SHM itself.
        # Inspect without connecting so all original recovery material survives.
        if not self._index_db.exists():
            return
        with self._index_db.open("rb") as source:
            header = source.read(16)
        sidecars = any(
            Path(str(self._index_db) + suffix).exists() for suffix in ("-wal", "-shm")
        )
        if header != b"SQLite format 3\x00" and (header or sidecars):
            raise sqlite3.DatabaseError("file is not a database")

    def _try_create_db(self) -> SQLiteConnection:
        self._check_database_header()
        database = sqlite3.connect(str(self._index_db), timeout=2.0)
        try:
            database.execute(_INDEX_SQL)
        except Exception:
            database.close()
            raise
        return database

    def _get_db(self) -> SQLiteConnection:
        try:
            return self._try_create_db()
        except (sqlite3.Error, OSError) as error:
            self._record_index_failure(error)
            raise

    def _repair_index(self, files: list[tuple[str, str]]) -> None:
        with _INDEX_REPAIR_LOCK:
            self._check_database_header()
            database = sqlite3.connect(str(self._index_db), timeout=2.0)
            try:
                tables = database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND sql NOT LIKE 'CREATE VIRTUAL TABLE%'"
                ).fetchall()
                for (table,) in tables:
                    if table == "memory_fts" or table.startswith("memory_fts_"):
                        continue
                    quoted = '"' + table.replace('"', '""') + '"'
                    checks = database.execute(
                        f"PRAGMA quick_check({quoted})"
                    ).fetchall()
                    if any(str(row[0]) != "ok" for row in checks):
                        raise sqlite3.DatabaseError(
                            "Non-index database pages failed integrity checks"
                        )
                with database:
                    database.execute("BEGIN IMMEDIATE")
                    database.execute("DROP TABLE IF EXISTS memory_fts")
                    database.execute(_INDEX_SQL)
                    database.executemany(
                        "INSERT INTO memory_fts (path, content) VALUES (?, ?)", files
                    )
            finally:
                database.close()

    @contextmanager
    def _database(self) -> Iterator[SQLiteConnection]:
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
        except (sqlite3.Error, OSError) as error:
            self._record_index_failure(error)

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
        except (sqlite3.Error, OSError) as error:
            self._record_index_failure(error)
            return 1
        missing = object()
        return sum(
            files.get(path, missing) != indexed.get(path, missing)
            for path in files.keys() | indexed.keys()
        )

    def rebuild_index(self) -> int:
        if not self._fts_available:
            return 0
        try:
            files = self._indexable_files()
            try:
                with self._database() as database:
                    database.execute("DELETE FROM memory_fts")
                    database.executemany(
                        "INSERT INTO memory_fts (path, content) VALUES (?, ?)", files
                    )
            except sqlite3.Error as error:
                code = str(getattr(error, "sqlite_errorname", "") or "")
                if any(
                    code.startswith(name)
                    for name in (
                        "SQLITE_BUSY",
                        "SQLITE_LOCKED",
                        "SQLITE_READONLY",
                        "SQLITE_FULL",
                        "SQLITE_CANTOPEN",
                        "SQLITE_IOERR",
                        "SQLITE_PERM",
                        "SQLITE_NOTADB",
                    )
                ):
                    raise
                self._repair_index(files)
        except (sqlite3.Error, OSError) as error:
            self._record_index_failure(error)
            return 0
        with _INDEX_FAILURES_LOCK:
            _INDEX_FAILURES.pop(str(self._index_db.resolve()), None)
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
        except (sqlite3.Error, OSError) as error:
            if "syntax error" not in str(error) and "unterminated string" not in str(
                error
            ):
                self._record_index_failure(error)
            return []
