# Knowledge and memory

These are two related but distinct subsystems. **Knowledge** is the user's
ingested content library (notes, documents, media) with a processing pipeline
and hybrid search. **Memory** is what the assistant learns and recalls across
conversations. Knowledge source paths below are relative to `runtime/gideon/cognition/`; other paths name their domain explicitly.

## Knowledge

### Retrieval configuration

`knowledge.fetch_top_n` defaults to **3** results and
`knowledge.fetch_max_tokens` defaults to **4096** estimated content tokens for
`GET /api/knowledge/search-for-context`. These settings survive config save/load;
request `limit` and `max_tokens` values override them for a single fetch.

`skills.progressive_disclosure_threshold` defaults to **2**. More matching skills
than this threshold produce a compact index; the agent uses `skill_invoke` to
retrieve full bodies. Zero disables progressive disclosure. The threshold is
clamped to `skills.max_triggered - 1` with a warning when too high, so the index
can activate within the match limit. With `max_triggered = 1`, the clamp yields
zero and the single skill is inlined.

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
`runtime/gideon/integrations/embedding_providers/registry.py::get_active_embed_fn()`, which resolves the
`embedding` use-case binding (Settings → Models). With nothing bound, embeddings
are gracefully off: no crash, and vector search simply does not participate. Any
provider works, from a configured local embedding app to any bound
remote model.

### Search

`knowledge/retrieval.py`: `HybridRetriever` fuses FTS5 keyword search, graph
traversal and optional vector search with reciprocal-rank fusion (RRF). A
minimum cosine floor keeps weak vector hits from polluting precise keyword
queries.

## Memory

### Provider and native authority

`memory_service.py` supplies policy and retrieval over the configured
`MemoryProvider` contract (`runtime/gideon/integrations/memory_providers/base.py`).
`memory_record.py` defines record kinds and their shared shape. Storage implementation
and record projections are not themselves permission to read another scope.

Hypermid memory integration is `runtime/gideon/hypermid/memory.py`, backed by the
authenticated local native client. Native owner/project/workspace scopes and per-operation
grants govern admitted reads and writes. App-scoped memory uses a native issued namespace
and current installed consent; it is not a workspace named by a caller.

Legacy `vector_memory.py`, `memory.py`, markdown projections and cwd-partition helpers
remain source consumers for their applicable configured paths. Do not describe a cwd
partition or a shared fallback as universal memory authority. A configured Hypermid
provider can refuse an operation that another storage adapter would support.

### Project, app and descendant reach

`runtime/gideon/security/session_credentials.py` resolves memory reach from verified
current work, original initiator, effective actor, lineage and privacy mode. Native
scope issuers and durable work origins are separate from transport session keys.
Children inherit a ceiling; they cannot choose an owner scope through a parent hint.
App work additionally intersects current memory consent and the live text/read/tools
tier. Text-tier work has no persistent-memory context.

Temporary suppresses persistent reads and writes; Incognito suppresses writes while
allowing otherwise authorized reads. A private workflow requires the native private-work
receipt and live lifetime, rather than conversion to another mode.

### Recall and the privacy guard

Recall routes live in `runtime/gideon/interfaces/dashboard/handlers/memory.py`.
Native operation admission checks actual proof and scope. Known typed authority refusals
are permission failures; unrelated invalid values or transport failures must not be
reported as authorized empty results.

Both ordinary context and Hypermid primary context honor effective read reach before
recall or summary expansion. Returned past content is framed as data, not instructions;
fencing does not prove the model will ignore every injected instruction. Independent
tool and write authority still matters. Restricted after-turn paths do not gain a
persistent write through consolidation, lessons or background capture.

### Human-readable projections

`memory_vault.py` implements the readable markdown vault. `memory.vault_mode` selects
`off`, `mirror` or `two_way`; projections and accepted edits must go through the memory
service's policy and event path. An unreadable page is reported rather than silently
merged. Files offered for knowledge ingestion are not automatically memory facts.
Provider capabilities determine which mutations and projections are available.

### Lexicon

`lexicon/` contains personal terms and transcription corrections. Provider-aware
transcription biases use bounded vocabulary and matching; a bound speech provider's
actual capabilities determine what is applied. The lexicon is distinct from memory
scope authority and does not grant model access to otherwise withheld records.

## Related docs

- Which model runs each pipeline stage: bindings in
  [OVERVIEW.md](OVERVIEW.md#extension-boundary)
- Event triggers that fire on memory writes:
  [TASKS_TRIGGERS.md](TASKS_TRIGGERS.md)
- The egress policy connectors fetch under: [SECURITY.md](SECURITY.md)
