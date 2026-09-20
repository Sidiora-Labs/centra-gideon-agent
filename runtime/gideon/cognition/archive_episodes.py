"""Episode persistence, optional vector indexing and recall projections."""

import json
import math
import struct


def contract():
    from gideon.cognition import vector_memory

    return vector_memory


class EpisodeIndex:
    def __init__(self, archive):
        self.store = archive
        self.api = contract()

    def reset(self):
        self.store._faiss_index = self.api.faiss.IndexFlatIP(self.store._embedding_dim)
        self.store._faiss_id_map = []

    def rebuild(self):
        api, store = self.api, self.store
        if not (api._HAS_FAISS and api._HAS_NUMPY):
            return 0
        self.reset()
        records = store.db.execute(
            "SELECT id, embedding FROM episodic_memories WHERE is_deleted = 0 AND embedding IS NOT NULL"
        ).fetchall()
        mismatches = 0
        for record in records:
            vector = api.np.frombuffer(record["embedding"], dtype=api.np.float32)
            if len(vector) == store._embedding_dim:
                store._faiss_index.add(vector.reshape(1, -1))
                store._faiss_id_map.append(record["id"])
            else:
                mismatches += 1
        if mismatches:
            api.logger.warning(
                "Skipped %d embeddings with mismatched dimension (expected %d)",
                mismatches,
                store._embedding_dim,
            )
        count = len(store._faiss_id_map)
        api.logger.info("Built FAISS index with %d vectors", count)
        return count

    def clear(self):
        cursor = self.store.db.execute(
            "UPDATE episodic_memories SET embedding = NULL WHERE embedding IS NOT NULL"
        )
        removed = cursor.rowcount
        self.store.db.commit()
        if self.api._HAS_FAISS:
            self.reset()
            self.store.save_faiss_index()
        self.api.logger.info("Cleared %d embeddings (model switch)", removed)
        return removed

    def save(self):
        store, api = self.store, self.api
        if not api._HAS_FAISS or store._faiss_index is None:
            return
        try:
            api.faiss.write_index(store._faiss_index, str(store._faiss_path))
            store._faiss_path.with_suffix(".ids.json").write_text(
                json.dumps(store._faiss_id_map), encoding="utf-8"
            )
            store._faiss_writes_since_save = 0
        except Exception:
            api.logger.warning("Failed to save FAISS index", exc_info=True)

    def load(self):
        store, api = self.store, self.api
        if not api._HAS_FAISS:
            return False
        paths = (store._faiss_path, store._faiss_path.with_suffix(".ids.json"))
        if all(path.exists() for path in paths):
            try:
                store._faiss_index = api.faiss.read_index(str(paths[0]))
                store._faiss_id_map = json.loads(paths[1].read_text(encoding="utf-8"))
                api.logger.info(
                    "Loaded FAISS index: %d vectors", len(store._faiss_id_map)
                )
                return True
            except Exception:
                api.logger.warning("FAISS index corrupted, rebuilding", exc_info=True)
        store.build_faiss_index()
        return False

    def reembed(self, progress):
        store, api = self.store, self.api
        totals = dict(reembedded=0, failed=0, total=0)
        if store.embed_fn is None:
            return totals
        records = store.db.execute(
            "SELECT id, text FROM episodic_memories WHERE is_deleted = 0 AND text IS NOT NULL AND text != ''"
        ).fetchall()
        totals["total"] = len(records)
        for ordinal, record in enumerate(records, 1):
            vector = store._try_embed(record["text"])
            successful = False
            if vector:
                try:
                    blob = api.np.array(vector, dtype=api.np.float32).tobytes()
                    store.db.execute(
                        "UPDATE episodic_memories SET embedding = ? WHERE id = ?",
                        (blob, record["id"]),
                    )
                    successful = True
                except Exception:
                    pass
            totals["reembedded" if successful else "failed"] += 1
            if progress is not None:
                progress(ordinal, len(records))
        store.db.commit()
        store.build_faiss_index()
        store.save_faiss_index()
        api.logger.info(
            "Re-embedded %d/%d episodic memories (%d failed)",
            totals["reembedded"],
            totals["total"],
            totals["failed"],
        )
        return totals

    def append(self, identity, blob):
        store, api = self.store, self.api
        if blob is None:
            return
        width = len(blob) // 4
        if width != store._embedding_dim:
            api.logger.warning(
                "Skipping FAISS add for episodic %s: embedding is %d-dim but the index is "
                "%d-dim. The memory itself is saved; run a re-embed to restore semantic "
                "recall for it. This usually means the embedding model changed without "
                "clearing the index.",
                identity,
                width,
                store._embedding_dim,
            )
            return
        if store._faiss_index is None:
            return
        vector = api.np.frombuffer(blob, dtype=api.np.float32).reshape(1, -1)
        store._faiss_index.add(vector)
        store._faiss_id_map.append(identity)
        store._faiss_writes_since_save += 1
        if store._faiss_writes_since_save >= api._FAISS_SAVE_INTERVAL:
            store.save_faiss_index()


class EpisodeAppend:
    def __init__(self, archive, text, source):
        self.store, self.text, self.source = archive, text.strip(), source
        self.api = contract()

    def encode(self, embedding):
        api = self.api
        if api._HAS_NUMPY:
            vector = api.np.array(embedding, dtype=api.np.float32)
            magnitude = api.np.linalg.norm(vector)
            if magnitude > 0:
                vector = vector / magnitude
            return vector.tobytes(), vector
        magnitude = math.sqrt(sum(value * value for value in embedding))
        vector = (
            [value / magnitude for value in embedding] if magnitude > 0 else embedding
        )
        return struct.pack(f"{len(vector)}f", *vector), vector

    def duplicate(self, vector):
        store = self.store
        index = store._faiss_index
        if index is None or index.ntotal <= 0 or len(vector) != store._embedding_dim:
            return False
        distances, addresses = index.search(vector.reshape(1, -1), 5)
        for similarity, address in zip(distances[0], addresses[0]):
            if address == -1:
                break
            if not float(similarity) > store._dedup_threshold:
                continue
            identity = store._faiss_id_map[int(address)]
            prior = store._get_episodic(identity)
            if prior and len(self.text) > len(prior["text"]) * 1.2:
                store._delete_episodic_row(identity)
                store._log_event(
                    "merge",
                    "episodic",
                    identity,
                    prior["text"][:200],
                    self.text[:200],
                    self.source,
                )
                return False
            store._log_event(
                "conflict_skip",
                "episodic",
                identity if prior else "?",
                "",
                self.text[:200],
                self.source,
            )
            return True
        return False

    def write(self, embedding, conversation, tags, importance, contributor):
        api, store, text = self.api, self.store, self.text
        if not api._EPISODIC_TEXT_MIN <= len(text) <= api._EPISODIC_TEXT_MAX:
            api.logger.debug(
                "Episodic rejected: len=%d (min=%d max=%d)",
                len(text),
                api._EPISODIC_TEXT_MIN,
                api._EPISODIC_TEXT_MAX,
            )
            return False
        normalized_tags = [
            tag.strip().lower()[:50] for tag in (tags or [])[:10] if tag.strip()
        ]
        importance = max(0.0, min(1.0, importance))
        prior = store.db.execute(
            "SELECT id FROM episodic_memories WHERE is_deleted = 0 AND LOWER(SUBSTR(text, 1, 80)) = ?",
            (text[:80].lower(),),
        ).fetchone()
        if prior:
            api.logger.debug(
                "Episodic text-hash dedup: prefix matches id=%s", prior["id"]
            )
            return False
        if embedding is None and store.embed_fn is not None:
            embedding = store._try_embed(text)
        blob = None
        if embedding is not None:
            blob, vector = self.encode(embedding)
            if self.duplicate(vector):
                return False
        store._enforce_episodic_cap()
        identity, timestamp = str(api.uuid4()), api._now_iso()
        store.db.execute(
            "INSERT INTO episodic_memories (id, conversation_id, text, embedding, tags, importance, created_at, is_deleted, contributor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                identity,
                conversation,
                text,
                blob,
                json.dumps(normalized_tags),
                importance,
                timestamp,
                api.current_username() if contributor is None else contributor,
            ),
        )
        store.db.commit()
        EpisodeIndex(store).append(identity, blob)
        store._log_event("create", "episodic", identity, None, text[:200], self.source)
        store.link_written_record(
            from_kind="episodic",
            from_ref=identity,
            text=text,
            batch_ref=conversation or None,
        )
        api.logger.debug(
            "Episodic written: id=%s src=%s imp=%.2f vec=%s text=%s…",
            identity[:8],
            self.source,
            importance,
            blob is not None,
            text[:80],
        )
        return True


class EpisodeRecall:
    fields = (
        "id",
        "conversation_id",
        "text",
        "tags",
        "importance",
        "created_at",
        "last_accessed_at",
    )

    def __init__(self, archive):
        self.store, self.api = archive, contract()

    def score(self, row, similarity, now):
        age = max(0, (now - self.api.datetime.fromisoformat(row["created_at"])).days)
        value = similarity * (0.7 + 0.3 * row["importance"]) * math.exp(-0.03 * age)
        return dict(score=round(value, 4), cosine_sim=round(similarity, 4))

    def finish(self, candidates, limit, diversify):
        candidates.sort(key=lambda item: item["score"], reverse=True)
        chosen = (
            self.api._mmr_rerank(candidates, limit=limit)
            if diversify
            else candidates[:limit]
        )
        for record in chosen:
            self.store.db.execute(
                "UPDATE episodic_memories SET last_accessed_at = ? WHERE id = ?",
                (self.api._now_iso(), record["id"]),
            )
        if chosen:
            self.store.db.commit()
        return chosen

    def search(self, embedding, text, limit, diversify, tags):
        api, store = self.api, self.store
        indexed = (
            embedding is not None
            and api._HAS_NUMPY
            and api._HAS_FAISS
            and store._faiss_index is not None
            and store._faiss_index.ntotal > 0
        )
        if indexed:
            return self.indexed(embedding, text, limit, diversify, tags)
        if embedding is not None:
            return store._sqlite_vector_search(
                embedding, text, limit, mmr=diversify, tag_filter=tags
            )
        api.logger.debug("Episodic keyword fallback: query=%s…", text[:60])
        return store._fts5_episodic_search(text, limit, tag_filter=tags) if text else []

    def indexed(self, embedding, text, limit, diversify, tags):
        api, store = self.api, self.store
        api.logger.debug(
            "Episodic FAISS search: query=%s… vectors=%d limit=%d",
            text[:60],
            store._faiss_index.ntotal,
            limit,
        )
        vector = api.np.array(embedding, dtype=api.np.float32)
        magnitude = api.np.linalg.norm(vector)
        if magnitude > 0:
            vector = vector / magnitude
        if vector.shape[0] != store._embedding_dim:
            api.logger.warning(
                "Semantic recall skipped: query is %d-dim but the index is %d-dim "
                "(embedding model changed). Falling back to keyword search; run a "
                "re-embed to restore semantic recall.",
                vector.shape[0],
                store._embedding_dim,
            )
            return []
        distances, addresses = store._faiss_index.search(
            vector.reshape(1, -1), min(limit * 2, store._faiss_index.ntotal)
        )
        now = api.datetime.now(tz=api.timezone.utc)
        candidates = []
        for similarity, address in zip(distances[0], addresses[0]):
            if address == -1:
                break
            row = store._get_episodic(store._faiss_id_map[int(address)])
            if (
                not row
                or row["is_deleted"]
                or (tags and not store._matches_tags(row, tags))
            ):
                continue
            candidates.append({**row, **self.score(row, float(similarity), now)})
        return self.finish(candidates, limit, diversify)

    def sqlite(self, embedding, text, limit, diversify, tags):
        magnitude = math.sqrt(sum(value * value for value in embedding))
        query = (
            [value / magnitude for value in embedding] if magnitude > 0 else embedding
        )
        rows = self.store.db.execute(
            "SELECT id, conversation_id, text, tags, importance, created_at, last_accessed_at, contributor, embedding "
            "FROM episodic_memories WHERE is_deleted = 0 AND embedding IS NOT NULL"
        ).fetchall()
        self.api.logger.debug(
            "Episodic SQLite vector search: query=%s… rows_with_emb=%d",
            text[:60],
            len(rows),
        )
        now = self.api.datetime.now(tz=self.api.timezone.utc)
        candidates = []
        for row in rows:
            width = len(row["embedding"]) // 4
            if width != len(query) or (
                tags and not self.store._matches_tags(dict(row), tags)
            ):
                continue
            vector = struct.unpack(f"{width}f", row["embedding"])
            similarity = sum(left * right for left, right in zip(query, vector))
            item = {name: row[name] for name in self.fields}
            item.update(self.score(row, similarity, now))
            candidates.append(item)
        return self.finish(candidates, limit, diversify)

    @staticmethod
    def tag_predicate(tags):
        if not tags:
            return "", ()
        return "(" + " OR ".join("tags LIKE ?" for _ in tags) + ")", tuple(
            f'%"{tag.lower()}"%' for tag in tags
        )

    def page(self, limit, offset, tags):
        predicate, params = self.tag_predicate(tags)
        suffix = " AND " + predicate if predicate else ""
        rows = self.store.db.execute(
            "SELECT "
            + ", ".join((*self.fields, "contributor"))
            + " FROM episodic_memories WHERE is_deleted = 0"
            + suffix
            + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return list(map(dict, rows))

    def keywords(self, query, limit, tags):
        terms = [
            word
            for word in query.strip().split()[:5]
            if 2 < len(word) <= self.api._LIKE_WORD_MAX_CHARS
        ]
        if not terms:
            return []
        clauses, params = [], []
        for column in ("text", "tags"):
            for term in terms:
                clauses.append(f"{column} LIKE ?")
                params.append(f"%{term}%")
        predicate = " OR ".join(clauses)
        tag_clause, tag_values = self.tag_predicate(tags)
        if tag_clause:
            predicate = f"({predicate}) AND {tag_clause}"
            params.extend(tag_values)
        try:
            rows = self.store.db.execute(
                "SELECT "
                + ", ".join((*self.fields, "contributor"))
                + " FROM episodic_memories WHERE is_deleted = 0 AND ("
                + predicate
                + ") ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        except self.api.sqlite3.OperationalError as exc:
            self.api.logger.warning(
                "Episodic keyword fallback degraded to no-matches: %s", exc
            )
            return []
        return list(map(dict, rows))

    def context(self, embedding, text, cap, citations):
        if embedding is None and text and self.store.embed_fn is not None:
            embedding = self.store._try_embed(text)
        rows = self.store.search_episodic(
            query_embedding=embedding, query_text=text, limit=self.store._episodic_limit
        )
        used = 0
        lines: list[str] = []
        for ordinal, row in enumerate(rows, 1):
            if "cosine_sim" in row:
                floor = (
                    self.api._EPISODIC_LONG_TEXT_THRESHOLD
                    if len(row.get("text", "")) > self.api._EPISODIC_LONG_TEXT_CHARS
                    else self.api._EPISODIC_RELEVANCE_THRESHOLD
                )
                if row["cosine_sim"] < floor:
                    continue
            excerpt, number = row["text"][:1500], len(lines) + 1
            label = f"[Memory {number}]" if citations is not None else f"{ordinal}."
            line = f"{label} {excerpt}"
            if used + len(line) > cap:
                break
            lines.append(line)
            used += len(line) + 1
            if citations is not None:
                identity = row.get("id")
                citations.append(
                    dict(
                        n=number,
                        id=str(identity) if identity is not None else None,
                        preview=excerpt[:120],
                    )
                )
        if not lines:
            return ""
        return (
            "[Episodic Memory — relevant past conversation fragments.]\n"
            + "\n".join(lines)
            + "\n[End of episodic memory]\n"
        )
