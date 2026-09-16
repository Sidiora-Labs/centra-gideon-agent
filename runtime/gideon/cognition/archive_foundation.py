"""Schema installation, lazy graph access, and typed record routing for an archive."""

from __future__ import annotations

import os
import threading


class ColumnExpansion:
    def __init__(self, database, driver, logger=None):
        self.database, self.driver, self.logger = database, driver, logger

    def add(self, table, declarations, *, report_existing=False):
        for column, declaration in declarations:
            try:
                self.database.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
                )
            except self.driver.OperationalError as error:
                if "duplicate column" not in str(error).lower():
                    raise
                if report_existing:
                    self.logger.debug("%s.%s already present", table, column)
        return self

    def index(self, name, table, column):
        self.database.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({column})")


def expand_axes(database, driver):
    layout = ColumnExpansion(database, driver)
    declarations = (
        ("tier", "TEXT"),
        ("scope", "TEXT DEFAULT 'global'"),
        ("scope_ref", "TEXT"),
        ("category", "TEXT"),
        ("visit_count", "INTEGER DEFAULT 0"),
    )
    for table, tier in (
        ("semantic_memory", "semantic"),
        ("episodic_memories", "episodic"),
    ):
        layout.add(table, declarations)
        database.execute(f"UPDATE {table} SET tier = ? WHERE tier IS NULL", (tier,))
    for name, table in (
        ("idx_semantic_scope", "semantic_memory"),
        ("idx_episodic_scope", "episodic_memories"),
    ):
        layout.index(name, table, "scope")


def install_entity_schema(database, schema, logger):
    database.executescript(schema)
    rows = database.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('knowledge_facts', 'knowledge_edges')"
    )
    legacy = [row[0] for row in rows.fetchall()]
    if legacy:
        logger.warning(
            "memory.db carries unexpected legacy table(s) %s — left untouched; "
            "the graph uses mem_* names. Report this if you see it.",
            ", ".join(legacy),
        )


class SchemaJournal:
    def __init__(self, database, logger, clock):
        self.database, self.logger, self.clock = database, logger, clock

    def apply(self, migrations):
        database = self.database
        database.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        database.commit()
        completed = frozenset(
            row[0]
            for row in database.execute("SELECT version FROM schema_version").fetchall()
        )
        pending = (
            (version, script, action)
            for version, script, action in migrations
            if version not in completed
        )
        for version, script, action in pending:
            if script:
                database.executescript(script)
            if action:
                action(database)
            database.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, self.clock()),
            )
            database.commit()
            self.logger.info("Applied memory schema migration v%s", version)


class ArchiveBootstrap:
    @staticmethod
    def state(owner):
        owner._db = None
        owner._db_lock = threading.Lock()
        for field in (
            "_faiss_index",
            "embed_fn",
            "contradiction_judge",
            "_ollama_manager",
            "_graph_enabled",
            "_graph",
            "_alias_index",
        ):
            setattr(owner, field, None)
        owner._faiss_id_map = []
        owner._faiss_writes_since_save = 0
        owner._alias_generation, owner._alias_index_gen = 0, -1

    @staticmethod
    def open(owner, driver, migrations, clock, logger):
        path = owner._db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = driver.connect(
            str(path), check_same_thread=False, isolation_level=None
        )
        owner._db = connection
        connection.row_factory = driver.Row
        for statement in ("PRAGMA journal_mode=WAL", "PRAGMA busy_timeout=5000"):
            connection.execute(statement)
        connection.isolation_level = ""
        SchemaJournal(connection, logger, clock).apply(migrations)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        try:
            owner.load_faiss_index()
        except Exception:
            logger.warning(
                "FAISS index not loaded (faiss-cpu may not be installed yet)",
                exc_info=True,
            )

    @staticmethod
    def close(owner):
        connection = owner._db
        if connection:
            connection.close()
            owner._db = None


class GraphAttachment:
    def __init__(self, archive):
        self.archive = archive

    def graph(self):
        from gideon.cognition.memory_graph import MemoryGraph

        owner = self.archive
        if getattr(owner, "_graph", None) is None:
            owner._graph = MemoryGraph(owner.db, log_event=owner._log_event)
        return owner._graph

    def aliases(self):
        owner = self.archive
        index = getattr(owner, "_alias_index", None)
        if index is not None and owner._alias_generation == getattr(
            owner, "_alias_index_gen", -1
        ):
            return index
        index = owner.graph.build_index()
        owner._alias_index, owner._alias_index_gen = index, owner._alias_generation
        return index


class TypedRecordWrite:
    def __init__(self, archive):
        self.archive = archive

    def semantic(self, record):
        content = record.value if record.value is not None else record.text
        refused = self.archive.set_semantic(
            record.id, content, record.confidence, record.source or "service"
        )
        if refused is None:
            return "semantic_memory", "key", record.id
        return None

    def episode(self, record):
        archive = self.archive
        identities = frozenset(
            row["id"]
            for row in archive.db.execute("SELECT id FROM episodic_memories").fetchall()
        )
        accepted = archive.write_episodic(
            record.text,
            embedding=record.embedding,
            conversation_id=record.conversation_id,
            tags=record.tags,
            importance=record.importance,
            source=record.source or "service",
        )
        if not accepted:
            return None
        latest = archive.db.execute(
            "SELECT id FROM episodic_memories ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        identity = latest["id"] if latest and latest["id"] not in identities else None
        return ("episodic_memories", "id", identity) if identity else None

    def apply(self, records):
        from gideon.cognition.memory_record import MemoryKind

        for record in records:
            write = (
                self.episode if record.kind == MemoryKind.EPISODIC else self.semantic
            )
            address = write(record)
            if address is not None:
                self.archive._apply_axes(*address, record)


class RecordAxes:
    def __init__(self, record):
        from gideon.cognition.memory_record import MemoryScope

        self.record = record
        self.tier = record.tier.value if record.tier else None
        self.scope = record.scope.value if record.scope else MemoryScope.GLOBAL.value
        self.untouched = (
            self.scope == MemoryScope.GLOBAL.value
            and record.scope_ref is None
            and record.category is None
            and not record.visit_count
            and self.tier is None
            and not record.recall_count
        )

    def persist(self, database, table, key_column, identity):
        if self.untouched:
            return
        record = self.record
        expressions = [
            "tier = COALESCE(?, tier)",
            "scope = ?",
            "scope_ref = ?",
            "category = ?",
            "visit_count = ?",
        ]
        parameters = [
            self.tier,
            self.scope,
            record.scope_ref,
            record.category,
            record.visit_count,
        ]
        if record.recall_count and table == "semantic_memory":
            expressions.append("recall_count = ?")
            parameters.append(record.recall_count)
        database.execute(
            f"UPDATE {table} SET {', '.join(expressions)} WHERE {key_column} = ?",
            (*parameters, identity),
        )
        database.commit()
