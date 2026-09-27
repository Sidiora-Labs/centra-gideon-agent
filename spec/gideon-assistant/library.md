# Library and research implementation

Give Gideon a searchable knowledge workspace for source material, reading, research, and source-linked results.
The compact assistant surface may show a document or citation card; the full Library opens in the shared
workspace frame with room for readers and graphs. Gideon's knowledge items, sources, reports, collections, and
artifacts remain the durable records.

The first implementation slice depends on **shell-01** and **delivery-01**. Conversation and Discovery provide
the result-card and Apps entry contracts as they become available. Each task below must use real account-scoped
records and preserve the source identity of every result.

- [ ] **library-01 — Route and data contract.** Add a labelled Research and Knowledge destination in the shared
      assistant workspace and connect it to Gideon's knowledge APIs.
  - Use `apps/assistant/src/features/library/LibraryWorkspace.web.tsx` for the responsive workspace and
    `apps/assistant/src/features/library/libraryApi.ts` for typed read/write operations.
  - Preserve native knowledge item, collection, source, report, and artifact IDs in routes; encode IDs in links
    and recover the selected view after reload or return to chat.
  - Separate the short assistant result card from the wide reader/graph layout. A narrow screen opens a
    full-screen Library route with reachable actions.
  - Treat no records, a failed read, an unavailable source, and an unauthorized record as different states. A
    failed refresh must not present cached data as current or imply records were deleted.
  - Acceptance: a signed-in user can open Library from Apps, follow a valid item link, reload it, and return to
    the same conversation and Library selection.

- [ ] **library-02 — Search, shelves, and curation.** Build the home, searchable item list, filters, favorites,
      reading state, and manual or saved-query collections over the existing knowledge store.
  - Use `apps/assistant/src/features/library/LibraryHome.web.tsx`, `LibrarySearch.web.tsx`, and
    `Collections.web.tsx`; keep collection operations in `libraryApi.ts`.
  - Show item type, title, source, date, processing state, and collection membership. Search must keep query,
    filter, and route state when a result opens and returns.
  - Support create, rename, reorder where available, add/remove items, and delete for collections with explicit
    confirmation for destructive actions. Preserve the knowledge item's identity when a shelf is removed.
  - Provide useful empty and no-match states, an explicit retry for failed search, and visible stale-result
    status. Search and collection controls need labels, focus order, and keyboard operation.
  - Acceptance: a user finds an existing item, places it in a collection, reloads, and sees the same membership
    and reading/favorite state from Gideon's records.

- [ ] **library-03 — Capture, import, and watched sources.** Bring notes, URLs, and supported files into the
      knowledge store and expose watched-source setup and health.
  - Use `apps/assistant/src/features/library/ImportPanel.web.tsx` and `SourcesWorkspace.web.tsx`; call the
    existing knowledge ingestion and source operations through `libraryApi.ts`.
  - A PDF upload must create or link a real knowledge item with its file, extraction state, original filename,
    and source provenance. Show progress and a recoverable error for rejected, interrupted, or unsupported
    files.
  - Source setup must preview the target where supported, show provider availability and collection choice, and
    distinguish scheduled, event-driven, paused, never-polled, and failed states.
  - Reconnect or configuration actions must respect account permissions. The UI must never describe an
    unenrolled source as collecting data.
  - Acceptance: a user imports a supported PDF or URL, sees the ingestion outcome, and later opens the resulting
    item by its durable ID; an unavailable source has a clear remedy.

- [ ] **library-04 — Reader, PDF view, and annotations.** Provide a document reader that keeps text, PDF,
      citations, highlights, and notes tied to the original item.
  - Use `apps/assistant/src/features/library/KnowledgeReader.web.tsx`, `PdfReader.web.tsx`, and
    `Annotations.web.tsx`, sharing the workspace frame without a narrow chat-width cap.
  - Show the extracted text or authenticated original file as appropriate. Keep page/reading position, outline,
    find, and zoom controls usable on desktop and touch screens.
  - Save annotations against stable item IDs and quote locations; show source title, URL/file origin, extraction
    timestamp, and citation target beside the passage.
  - An annotation write failure must retain the draft and offer retry. A missing original file or unavailable
    extraction must show which representation is still readable.
  - Acceptance: a user opens an imported PDF, reads it, adds a note, reloads, and returns to that source-linked
    note with an accessible keyboard path.

- [ ] **library-05 — Knowledge graph and provenance.** Expose related items and entities in a navigable graph
      with a textual equivalent.
  - Use `apps/assistant/src/features/library/KnowledgeGraph.web.tsx` and `ProvenancePanel.web.tsx`; consume
    Gideon's existing graph and item-relation projections.
  - Nodes and edges must retain native IDs and relation types. Selecting a node opens its detail or reader;
    returning restores graph selection and viewport where feasible.
  - Every inferred relation needs a visible source or derivation label. Show an honest empty graph when no links
    exist, and show a retryable failure when projection cannot load.
  - Provide keyboard traversal, zoom controls, readable labels, and an equivalent relationship list for
    reduced-motion or nonvisual use.
  - Acceptance: a user can trace a result to a linked source, open it, and return to the same graph context
    without losing provenance.

- [ ] **library-06 — Research reports and cited results.** Let a user create, run, inspect, and manage saved
      research reports using Gideon's report records and citation policy.
  - Use `apps/assistant/src/features/library/ResearchReports.web.tsx` and `ResearchResult.web.tsx`; report
    actions use `libraryApi.ts` rather than a second report store.
  - Show scope, source filters, schedule, last run, current status, error, and citation policy. A report result
    opens its saved knowledge item and the sources it cites.
  - Report run and pause actions must show accepted state from the server; failed runs retain the previous
    report and a retry route. Never invent source counts or completed status.
  - Acceptance: a report can be saved, run, revisited after reload, and traced from a citation to the underlying
    knowledge item or original source.

- [ ] **library-07 — Result return, export, and continuity.** Connect Library results to Conversation and
      Activity and let users export permitted material with provenance.
  - Use `apps/assistant/src/features/library/LibraryResultCard.web.tsx`, `LibraryExport.web.tsx`, and
    `libraryRoutes.ts`; hand result cards native item/artifact IDs and source links.
  - A chat or task result opens the correct reader, report, or artifact; the return action restores the
    originating conversation or Activity context. Do not store temporary file URLs in durable messages.
  - Offer the existing permitted original-file or rendered export, with filename, format, and source
    attribution. An export failure must leave the item open with a retry action.
  - Link source-derived memories to Personal's memory inspection destination when such a record exists; Library
    does not own the memory editor or personal journal record.
  - Acceptance: a research result from chat opens its exact saved item or artifact, exports the permitted
    version, and returns to the same transcript position on desktop and mobile web.

Implementation qualification should exercise these journeys with real Gideon records and connected or
unavailable source states. Preserve established source permissions and the existing account boundary when
adapting the same Library implementation for hosted delivery.
