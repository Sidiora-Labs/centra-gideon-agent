# Knowledge and memory

These are two related but distinct subsystems. **Knowledge** is the user's
ingested content library (notes, documents, media) with a processing pipeline
and hybrid search. **Memory** is what the assistant learns and recalls across
conversations. Paths are relative to `Gideon/src/gideon/`.

## Knowledge

### Store

`knowledge/store.py`: `knowledge.db` (SQLite) holds items, an FTS index, and
embedding vectors. The raw embedding vector never leaves the DB: API responses
carry only a `has_embedding` flag. Deleting an item cleans up its vectors,
mentions and FTS rows, not just the item row. FTS values are kept in sync on
update by deleting with the old values and inserting with the new.

### Project scoping

Knowledge stays one global library. A project is a **tag plus item metadata**,
never a second database. `knowledge/project_scope.py` owns that scoping for the
items a workflow run writes: `project_id` (the producing container, first writer
wins), `run_id`, and a closed `sharing_policy` (`private` | `shared`, default
private). A project's Knowledge view shows its own items whatever their policy,
plus other projects' `shared` items labeled with their source project; another
project's private items never appear. The project tag is the same one
`knowledge/session_brief.py::project_tag` reads for a run's project brief.

### Ingestion pipeline (node graphs)

`knowledge/pipeline/` is a node-graph executor:

- **`graphs.py`** maps each of the 12 native item types to a code-owned
  `PipelineGraph` subclass. Users tune per-node execution parameters
  (enable/backend/use-case/timeout) via config but **cannot rewire a graph**.
  - Text types (`note`, `gist`, `journal`, `fleeting`) → `PassthroughGraph`
    (the content *is* the extracted text).
  - `bookmark` → `BookmarkGraph` (scrape the URL; user-pasted content passes
    through without a fetch).
  - Document types (`pdf`, `document`, `sheet`, `slides`) → `DocumentGraph`
    (pure-python file read → consolidate).
  - Media types (`image`, `audio`, `video`) → media graphs (`ImageGraph` runs
    exif ∥ ocr + vision). Model-backed nodes degrade gracefully: with no bound
    vision model the node is skipped, never a hard failure.
- **Terminal stages are not graph nodes.** After a graph completes,
  `pipeline/runner.py` runs consolidate-pool → insights → chunk+embed once over
  the whole extracted-content bundle, because they operate on the item bundle
  rather than on a single node's input.
- **`knowledge/insights.py`** produces `{summary, key_points, topics,
  action_items}`; entity and intent extraction follow. The AI title is
  **opt-in for user files**: a user-supplied filename survives enrichment until
  `file_metadata.original_filename` no longer matches.
- **Readers** (`knowledge/readers.py`) cover the 12 create formats.
  `knowledge/connectors/web_url.py` fetches bookmark/URL content through the
  egress chokepoint (`net_fetch` with `egress_policy_for(CONNECTOR)`; see
  [SECURITY.md](SECURITY.md)). `knowledge/dedup.py` deduplicates, and
  `knowledge/llm_pool.py` pools background LLM workers.

### Embedding

`knowledge/embedder.py`: `UnifiedEmbedder` is the one provider-agnostic
embedding path. It wraps
`embedding_providers/registry.py::get_active_embed_fn()`, which resolves the
`embedding` use-case binding (Settings → Models). With nothing bound, embeddings
are gracefully off: no crash, and vector search simply does not participate. Any
provider works, from the native `apps/sentence-transformers` app to any bound
remote model.

### Search

`knowledge/retrieval.py`: `HybridRetriever` fuses FTS5 keyword search, graph
traversal and optional vector search with reciprocal-rank fusion (RRF). A
minimum cosine floor keeps weak vector hits from polluting precise keyword
queries.

## Memory

### Stores

- **`vector_memory.py`**: semantic and episodic memory. The FAISS index at
  `~/.gideon/memory.faiss` is optional: without embeddings it degrades to FTS5.
  It also owns time-decay retrieval and the config-threaded episodic knobs
  (`episodic_dedup_threshold`, `episodic_max_results` in `config/loader.py`).
- **`memory.py`**: structured key/value memory with FTS5.
- **`memory_record.py`**: the typed `MemoryRecord` with a `kind`
  discriminator, the one shape the subsystem speaks. The key taxonomy is
  prefix-based: `pref.*` / `project.*` keys are semantic facts; `lesson.*`
  keys are corrective rules; `user.procedural.*` / `user.persona.*` /
  `user.commitment.*` are their own kinds.
- **`memory_service.py`**: the service layer, including **promotion**.
  Session-scoped records are swept at session end unless they are sealed or
  promoted, and `promote_by_heat` is the conservative global gate that promotes
  only records whose accumulated heat crosses the threshold, which protects
  against one-off session noise.
- **`memory_vault.py`**: the human-readable markdown vault. `memory.vault_mode`
  picks `off` / `mirror` (projection only) / `two_way`, where hand edits are read
  back through `MemoryService.apply_vault_edit`: the normal semantic write path
  with the S5 scan and a reversible `memory_events` row. Every page carries a
  `source_hash` of its body, which is what makes an edit detectable and a
  frontmatter rewrite invisible. A page the parser cannot read is left alone and
  flagged rather than merged. Files dropped in `<vault>/raw/` are routed to the
  Knowledge ingest queue, never into memory, so the boundary holds inside the
  vault.
- **`learn.py`**: lesson capture; **`memory_lint.py`**: hygiene checks;
  **`engagement_signals.py`** and **`preference_facets.py`**: derived preference
  data.

### Partitions and project locality

- Memory is partitioned by **working directory**: `config/loader.py`'s
  `memory_dir_for_cwd(cwd)` maps a session's cwd onto
  `~/.gideon/workspace/_ext/<slug(cwd)>`, and an empty cwd onto the shared
  `_ext/_default` partition. `context.py::PromptAssembler.get_memory_for`
  resolves and caches one store per partition, and the gateway's own workspace
  is aliased onto the main store, so a dashboard chat and the Memory UI share
  one.
- **Project locality rides that seam** (`memory_locality.py`): a project-owned
  run binds the project's `context_dir` as its cwd, so what it learns lands in
  that project's partition instead of the shared pile.
- Recall for a project-local session is **partition-first**: its own partition,
  then the global partition, whose hits are source-labeled and fenced
  (`security.py::fence_untrusted`). This affects **ordering only, never
  admission**. A hit that exists only in the global partition is still returned,
  on its own if need be.

### Recall and the privacy guard

- Recall handlers live in `dashboard/handlers/memory.py`. Restricted sessions
  are enforced at the API layer: a **temporary** session blocks memory reads
  (`_blocks_reads_session`), and both temporary and **incognito** block writes
  (`_is_restricted_session`). See
  [CHAT_SESSIONS.md](CHAT_SESSIONS.md#session-model).
- Recalled episodic content is fenced as data. The recall block opens with a
  bracket header naming the content as past conversation fragments and marking it
  `(DATA, not instructions)`, so a poisoned memory cannot smuggle instructions
  into the prompt. The generic fencing helper for untrusted content is
  `security.py::fence_untrusted` (see [SECURITY.md](SECURITY.md)).
- The after-turn learning path (`after_turn_review.py`) is gated on
  `session.is_restricted`: restricted sessions never write lessons.

### Lexicon

`lexicon/` holds user terms and learned corrections in `lexicon.db`. The lexicon
biases all speech transcription, within a hard budget of about 64 terms and 200
characters, because Whisper's initial-prompt window is 224 tokens and
overflowing it silently empties transcripts. Graph resync prunes stale terms
while preserving user-pruned flags.

## Related docs

- Which model runs each pipeline stage: bindings in
  [OVERVIEW.md](OVERVIEW.md#capability-seams)
- Event triggers that fire on memory writes:
  [TASKS_TRIGGERS.md](TASKS_TRIGGERS.md)
- The egress policy connectors fetch under: [SECURITY.md](SECURITY.md)
