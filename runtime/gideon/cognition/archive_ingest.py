"""Legacy input adapters and bounded episodic-to-semantic promotion."""

import json
import re
import time


def contract():
    from gideon.cognition import vector_memory

    return vector_memory


class ArchiveImport:
    def __init__(self, archive):
        self.store = archive
        self.api = contract()
        self.counts = dict(semantic=0, episodic=0, skipped=0)

    def count(self, kind, accepted):
        self.counts[kind if accepted else "skipped"] += 1

    def episode(self, text, importance, tags):
        return self.store.write_episodic(
            text,
            embedding=self.store._try_embed(text),
            importance=importance,
            source="migration",
            tags=tags,
        )

    @staticmethod
    def lines(path):
        if path.is_file():
            return [
                line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            ]
        return []

    def lessons(self, path):
        for line in self.lines(path):
            if not line:
                continue
            try:
                payload = json.loads(line)
                rule = payload.get("rule", "")
                negative = payload.get("negative")
                accepted = rule and self.store.write_lesson(
                    rule,
                    payload.get("category", "knowledge"),
                    negative,
                    source="migration",
                )
                self.count("semantic", accepted)
            except (json.JSONDecodeError, KeyError):
                self.counts["skipped"] += 1

    def preferences(self, path):
        for line in self.lines(path):
            if not line.startswith("- "):
                continue
            text = line[2:].strip()
            if not text:
                continue
            parsed = self.store._parse_preference(text)
            if parsed is not None:
                key, value = parsed
                if self.store.set_semantic(key, value, 0.85, "migration") is None:
                    self.counts["semantic"] += 1
                    continue
            self.count("episodic", self.episode(text, 0.6, ["preference"]))

    def projects(self, path):
        project = ""
        for line in self.lines(path):
            if not line.startswith("- "):
                continue
            if ":" in line:
                name = line[2:].split(":")[0].strip()
                project = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
                self.count(
                    "semantic",
                    self.store.set_semantic("project.name", name, 0.85, "migration")
                    is None,
                )
            elif project:
                text = line[2:].strip()
                self.count(
                    "episodic", text and self.episode(text, 0.5, ["project", project])
                )

    def history(self, directory):
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.md")):
            content = path.read_text(encoding="utf-8", errors="replace")
            for fragment in re.split(r"\n(?=\[[\d-]+)", content):
                text = fragment.strip()
                if (
                    not text
                    or text.startswith(("#", "<!--"))
                    or len(text) < self.api._EPISODIC_TEXT_MIN
                ):
                    continue
                self.count(
                    "episodic",
                    self.episode(text[: self.api._EPISODIC_TEXT_MAX], 0.4, ["history"]),
                )

    def markdown(self):
        base = self.api._path_home_gideon() / "workspace" / "memory"
        self.lessons(self.api._path_home_gideon() / "lessons.jsonl")
        for name, reader in (
            ("preferences.md", self.preferences),
            ("projects.md", self.projects),
            ("history", self.history),
        ):
            reader(base / name)
        embedded = self.store.db.execute(
            "SELECT COUNT(*) FROM episodic_memories WHERE is_deleted=0 AND embedding IS NOT NULL"
        ).fetchone()[0]
        self.api.logger.info(
            "Migration complete: semantic=%d episodic=%d skipped=%d embedded=%d",
            self.counts["semantic"],
            self.counts["episodic"],
            self.counts["skipped"],
            embedded,
        )
        return self.counts

    def semantic_entry(self, entry):
        value = (
            json.loads(entry["value_json"])
            if isinstance(entry.get("value_json"), str)
            else entry.get("value")
        )
        return (
            self.store.set_semantic(
                entry["key"],
                value,
                float(entry.get("confidence", 0.85)),
                entry.get("source", "import"),
                contributor=str(entry.get("contributor") or "") or None,
            )
            is None
        )

    def episodic_entry(self, entry):
        return self.store.write_episodic(
            entry["text"],
            embedding=self.store._try_embed(entry["text"]),
            importance=float(entry.get("importance", 0.5)),
            source=entry.get("source", "import"),
            tags=(
                json.loads(entry["tags"])
                if isinstance(entry.get("tags"), str)
                else entry.get("tags", [])
            ),
            contributor=str(entry.get("contributor") or "") or None,
        )

    def document(self, data):
        for kind, writer in (
            ("semantic", self.semantic_entry),
            ("episodic", self.episodic_entry),
        ):
            for entry in data.get(kind, []):
                try:
                    self.count(kind, writer(entry))
                except Exception:
                    self.counts["skipped"] += 1
        return self.counts


class PatternPromotion:
    def __init__(self, archive):
        self.store, self.api = archive, contract()

    def clusters(self, threshold):
        rows = self.store.db.execute(
            "SELECT id, conversation_id, text, embedding, importance, created_at, visit_count FROM episodic_memories "
            "WHERE is_deleted = 0 AND embedding IS NOT NULL ORDER BY importance DESC, created_at DESC LIMIT 500"
        ).fetchall()
        usable, stale = [], 0
        for row in rows:
            vector = self.api.np.frombuffer(row["embedding"], dtype=self.api.np.float32)
            if len(vector) == self.store._embedding_dim:
                usable.append((dict(row), vector))
            else:
                stale += 1
        if stale:
            self.api.logger.warning(
                "Consolidation skipped %d episodic memories embedded at a different "
                "dimension than the current model (%d). Re-embed to include them.",
                stale,
                self.store._embedding_dim,
            )
        representatives, groups = [], []
        for row, vector in usable:
            destination = None
            for index, representative in enumerate(representatives):
                if float(self.api.np.dot(vector, representative)) > threshold:
                    destination = index
                    break
            if destination is None:
                representatives.append(vector)
                groups.append([row])
            else:
                groups[destination].append(row)
        return groups

    def candidates(self, clusters, minimum, unique_queries, floor):
        now = time.time()
        ranked = []
        for members in clusters:
            if len(members) < minimum:
                continue
            signals = self.api.dream_score(members, now_ts=now)
            if signals["unique_queries"] < unique_queries or signals["score"] < floor:
                self.api.logger.debug(
                    "Promotion skipped (gate): score=%.3f uniq=%d n=%d",
                    signals["score"],
                    signals["unique_queries"],
                    len(members),
                )
            else:
                ranked.append((signals["score"], members))
        ranked.sort(key=lambda candidate: -candidate[0])
        return ranked

    def run(self, minimum, similarity, maximum, floor, unique_queries):
        if not self.store.embed_fn or not self.api._HAS_NUMPY:
            self.api.logger.info("Promotion skipped: embeddings not available")
            return 0
        candidates = self.candidates(
            self.clusters(similarity), minimum, unique_queries, floor
        )
        written = 0
        for score, members in candidates:
            text = max(members, key=lambda row: len(row["text"]))["text"]
            key = self.store._infer_semantic_key(text)
            if not key:
                continue
            value = self.store._extract_value_from_text(text)
            if self.store.set_semantic(key, value, 0.9, "promotion") is not None:
                continue
            written += 1
            for row in members:
                self.store._delete_episodic_row(row["id"])
            self.api.logger.info(
                "Promoted %d episodic → %s (dream_score=%.3f): %s",
                len(members),
                key,
                score,
                value[:60],
            )
            if maximum is not None and written >= maximum:
                self.api.logger.info("Promotion run hit per-run cap (%d)", maximum)
                break
        return written
