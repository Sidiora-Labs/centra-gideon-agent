"""The state inventory — one manifest of everything that matters (§1).

Every durability mechanism (snapshot, export, and later shard/sync/drills) reads
THIS instead of carrying its own allowlist. That is the whole point: the previous
design had `snapshot.CORE_FILES` and `portability.EXPORT_EXCLUDE` maintained by
hand, and they had already drifted — nine real store directories were covered by
neither. A declarative manifest plus :func:`audit_home` (which fails on any
unclaimed path) makes that class of bug impossible to reintroduce silently.

Each entry declares four things that matter to a backup:

* **kind** — how to read/write it safely. ``sqlite`` in particular must never be
  raw-copied while the gateway holds it open; see :func:`sqlite_entries`.
* **domain** — the user-facing grouping. Snapshot components are exactly the
  domains, so ``VALID_COMPONENTS`` is derived, never typed twice.
* **secret** — never leaves this machine, in any export or sync.
* **derived** — an index/cache rebuilt from authoritative state. Excluded from
  shards and exports; restoring it is at best wasted bytes and at worst a
  corrupt index paired with a newer store.

``merge`` and ``tombstones`` are declared here but consumed by later sessions
(restore --mode merge, shard sync); they are part of the entry's identity, so
they belong in the manifest rather than being bolted on later.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


KIND_JSON_ENTITY_DIR = "json_entity_dir"
KIND_JSON_FILE = "json_file"
KIND_JSONL_APPEND = "jsonl_append"
KIND_SQLITE = "sqlite"
KIND_TREE = "tree"
KINDS = (
    KIND_JSON_ENTITY_DIR,
    KIND_JSON_FILE,
    KIND_JSONL_APPEND,
    KIND_SQLITE,
    KIND_TREE,
)

DOMAIN_MEMORY = "memory"
DOMAIN_KNOWLEDGE = "knowledge"
DOMAIN_WORK = "work"
DOMAIN_AUTOMATION = "automation"
DOMAIN_PLATFORM = "platform"
DOMAIN_CONFIG = "config"
DOMAIN_SECURITY = "security"
DOMAINS = (
    DOMAIN_MEMORY,
    DOMAIN_KNOWLEDGE,
    DOMAIN_WORK,
    DOMAIN_AUTOMATION,
    DOMAIN_PLATFORM,
    DOMAIN_CONFIG,
    DOMAIN_SECURITY,
)

MERGE_UNION_BY_ID = "union_by_id"
MERGE_LWW = "lww_by_updated_at"
MERGE_APPEND_DEDUP = "append_dedup"
MERGE_SQLITE_ATTACH_IGNORE = "sqlite_attach_ignore"
MERGE_REPLACE_ONLY = "replace_only"
MERGES = (
    MERGE_UNION_BY_ID,
    MERGE_LWW,
    MERGE_APPEND_DEDUP,
    MERGE_SQLITE_ATTACH_IGNORE,
    MERGE_REPLACE_ONLY,
)


@dataclass(frozen=True)
class StateEntry:
    """One declared piece of Gideon's state."""

    id: str
    kind: str
    path: str
    domain: str
    merge: str
    secret: bool = False
    derived: bool = False
    tombstones: bool = False
    db_container: bool = False
    help: str = ""
    derived_within: tuple[str, ...] = field(default_factory=tuple)


INVENTORY: tuple[StateEntry, ...] = (
    StateEntry(id="capability_media_sprites", kind=KIND_SQLITE, path="capabilities/media/sprites.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_inference_host", kind=KIND_JSON_FILE, path="capabilities/platform/inference_host.json", domain=DOMAIN_CONFIG, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_peer_identity", kind=KIND_TREE, path="capabilities/platform/peers/identity.key", domain=DOMAIN_SECURITY, merge=MERGE_REPLACE_ONLY, secret=True),
    StateEntry(id="capability_peer_policy", kind=KIND_SQLITE, path="capabilities/platform/peers/peers.sqlite3", domain=DOMAIN_SECURITY, merge=MERGE_REPLACE_ONLY, secret=True),
    StateEntry(id="capability_wellbeing_shared", kind=KIND_JSON_FILE, path="shared/wellbeing.json", domain=DOMAIN_KNOWLEDGE, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_privacy", kind=KIND_SQLITE, path="capabilities/privacy.sqlite3", domain=DOMAIN_SECURITY, merge=MERGE_REPLACE_ONLY, secret=True),
    StateEntry(id="capability_workspace_native_paired_devices", kind=KIND_SQLITE, path="capabilities/workspace/native/paired-devices.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_music_decks", kind=KIND_SQLITE, path="capabilities/music/decks.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_media_episodes", kind=KIND_SQLITE, path="capabilities/media/episodes.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_media_timelines", kind=KIND_SQLITE, path="capabilities/media/timelines.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_music_spotify", kind=KIND_SQLITE, path="capabilities/music/spotify.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_music_listening", kind=KIND_SQLITE, path="capabilities/music/listening.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_identity_lifecycle", kind=KIND_SQLITE, path="capabilities/identity/lifecycle.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_models_loras", kind=KIND_TREE, path="models/loras", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_models_lora_training", kind=KIND_TREE, path="models/lora-training", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_platform_cadence", kind=KIND_JSON_FILE, path="capabilities/platform/cadence.json", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_platform_pr_screening", kind=KIND_TREE, path="capabilities/platform/pr_screening", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_platform_maintenance", kind=KIND_TREE, path="capabilities/platform/maintenance", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_workspace_desktops_project_operations", kind=KIND_SQLITE, path="capabilities/workspace/desktops/project-operations.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_workspace_desktops_desktop_sessions", kind=KIND_SQLITE, path="capabilities/workspace/desktops/desktop-sessions.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_media_datasets", kind=KIND_SQLITE, path="capabilities/media/datasets.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_music_assemblies", kind=KIND_SQLITE, path="capabilities/music/assemblies.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_music_image3d", kind=KIND_SQLITE, path="capabilities/music/image3d.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_identity_bundles", kind=KIND_SQLITE, path="capabilities/identity/bundles.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_identity_recipes", kind=KIND_SQLITE, path="capabilities/identity/recipes.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_music_music_video", kind=KIND_SQLITE, path="capabilities/music/music_video.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_identity_continuity", kind=KIND_SQLITE, path="capabilities/identity/continuity.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_workspace_git_git_operations", kind=KIND_SQLITE, path="capabilities/workspace/git/git-operations.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_workspace_git_project_operations", kind=KIND_SQLITE, path="capabilities/workspace/git/project-operations.sqlite3", domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_music_midi", kind=KIND_SQLITE,
               path="capabilities/music/midi.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_workspace_projects_project_operations", kind=KIND_SQLITE,
               path="capabilities/workspace/projects/project-operations.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_identity_goals", kind=KIND_SQLITE,
               path="capabilities/identity/goals.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_identity_progress", kind=KIND_SQLITE,
               path="capabilities/identity/progress.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_music_rounds", kind=KIND_SQLITE,
               path="capabilities/music/rounds.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_platform_feature_ownership", kind=KIND_SQLITE,
               path="capabilities/platform/feature_ownership.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_media_readiness", kind=KIND_SQLITE,
               path="capabilities/media/readiness.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY),
    StateEntry(id="capability_workspace_processes_processes", kind=KIND_SQLITE,
               path="capabilities/workspace/processes/processes.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_workspace_ports_ports", kind=KIND_SQLITE,
               path="capabilities/workspace/ports/ports.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_identity_fidelity", kind=KIND_SQLITE,
               path="capabilities/identity/fidelity.sqlite3", domain=DOMAIN_MEMORY,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_music_generation", kind=KIND_SQLITE,
               path="capabilities/music/generation.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_media_annotations", kind=KIND_SQLITE,
               path="capabilities/media/annotations.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_media_jobs", kind=KIND_SQLITE,
               path="capabilities/media/jobs.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_platform_reviews", kind=KIND_SQLITE,
               path="capabilities/platform/reviews.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_mailbox_sources", kind=KIND_TREE, path="capabilities/communications/mailboxes",
               domain=DOMAIN_WORK, merge=MERGE_REPLACE_ONLY, help="Imported mailbox source files"),
    StateEntry(id="capability_workspace_snapshots", kind=KIND_SQLITE,
               path="capabilities/workspace/snapshots.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_identity_stories", kind=KIND_SQLITE,
               path="capabilities/identity/stories.sqlite3", domain=DOMAIN_MEMORY,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_identity_twin", kind=KIND_SQLITE,
               path="capabilities/identity/twin.sqlite3", domain=DOMAIN_MEMORY,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_wellbeing", kind=KIND_SQLITE,
               path="capabilities/wellbeing.sqlite3", domain=DOMAIN_KNOWLEDGE,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_communications_people", kind=KIND_SQLITE,
               path="capabilities/communications/people.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_media_sketches", kind=KIND_SQLITE,
               path="capabilities/media/sketches.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_creative_catalog", kind=KIND_SQLITE,
               path="capabilities/creative/catalog.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_music_repertoire", kind=KIND_SQLITE,
               path="capabilities/music/repertoire.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_music_music_catalog", kind=KIND_SQLITE,
               path="capabilities/music/music_catalog.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(id="capability_experience", kind=KIND_SQLITE,
               path="capabilities/experience.sqlite3", domain=DOMAIN_WORK,
               merge=MERGE_REPLACE_ONLY, help="Capability records and immutable revision history"),
    StateEntry(
        id="rooms",
        kind=KIND_TREE,
        path="rooms",
        domain=DOMAIN_WORK,
        merge=MERGE_REPLACE_ONLY,
        help="agent room records and conversation transcripts",
    ),
    StateEntry(
        id="memory_db",
        kind=KIND_SQLITE,
        path="memory.db",
        domain=DOMAIN_MEMORY,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="semantic facts, episodes, lessons, memory events",
    ),
    StateEntry(
        id="memory_index_db",
        kind=KIND_SQLITE,
        path="memory_index.db",
        domain=DOMAIN_MEMORY,
        merge=MERGE_REPLACE_ONLY,
        derived=True,
        help="memory search index (rebuilt from memory.db)",
    ),
    StateEntry(
        id="memory_faiss",
        kind=KIND_TREE,
        path="memory.faiss",
        domain=DOMAIN_MEMORY,
        merge=MERGE_REPLACE_ONLY,
        derived=True,
        help="vector index (rebuilt from embeddings)",
    ),
    StateEntry(
        id="memory_ids",
        kind=KIND_JSON_FILE,
        path="memory.ids.json",
        domain=DOMAIN_MEMORY,
        merge=MERGE_REPLACE_ONLY,
        derived=True,
        help="vector index id map (rebuilt with the index)",
    ),
    StateEntry(
        id="memory_vault",
        kind=KIND_TREE,
        path="memory-vault",
        domain=DOMAIN_MEMORY,
        merge=MERGE_REPLACE_ONLY,
        help="readable markdown vault (browsable memory; may hold unsynced edits)",
    ),
    StateEntry(
        id="knowledge_db",
        kind=KIND_SQLITE,
        path="workspace/knowledge/knowledge.db",
        domain=DOMAIN_KNOWLEDGE,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="knowledge items, entities, extractions",
    ),
    StateEntry(
        id="knowledge_files",
        kind=KIND_TREE,
        path="workspace/knowledge/files",
        domain=DOMAIN_KNOWLEDGE,
        merge=MERGE_UNION_BY_ID,
        help="original uploaded documents behind knowledge items",
    ),
    StateEntry(
        id="lexicon_db",
        kind=KIND_SQLITE,
        path="workspace/lexicon/lexicon.db",
        domain=DOMAIN_KNOWLEDGE,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="learned vocabulary / term lexicon",
    ),
    StateEntry(
        id="tasks",
        kind=KIND_JSON_ENTITY_DIR,
        path="tasks",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        tombstones=True,
        help="tasks, task lists, and task comments",
    ),
    StateEntry(
        id="projects",
        kind=KIND_JSON_ENTITY_DIR,
        path="projects",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        tombstones=True,
        help="projects and their briefs/context",
        derived_within=("*/worktrees",),
    ),
    StateEntry(
        id="loops_db",
        kind=KIND_SQLITE,
        path="loop/loops.db",
        domain=DOMAIN_WORK,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="autonomous run records",
    ),
    StateEntry(
        id="loop",
        kind=KIND_TREE,
        path="loop",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="autonomous run findings, verdicts, per-run files",
        derived_within=("loops.db",),
    ),
    StateEntry(
        id="artifacts",
        kind=KIND_TREE,
        path="artifacts",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="saved artifacts and their version history",
    ),
    StateEntry(
        id="doc_comments",
        kind=KIND_JSON_FILE,
        path="doc_comments.json",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="comments anchored to documents",
    ),
    StateEntry(
        id="sessions",
        kind=KIND_JSONL_APPEND,
        path="sessions",
        domain=DOMAIN_WORK,
        merge=MERGE_APPEND_DEDUP,
        help="chat transcripts",
    ),
    StateEntry(
        id="subagents",
        kind=KIND_TREE,
        path="subagents",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="subagent run records",
    ),
    StateEntry(
        id="uploads",
        kind=KIND_TREE,
        path="uploads",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="files uploaded through chat",
    ),
    StateEntry(
        id="code",
        kind=KIND_TREE,
        path="code",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="code-loop working checkouts",
        derived=True,
    ),
    StateEntry(
        id="turn_checkpoints",
        kind=KIND_TREE,
        path="checkpoints",
        domain=DOMAIN_WORK,
        merge=MERGE_REPLACE_ONLY,
        help="pre-edit file backups behind /rewind-to-turn (machine-local, session-scoped)",
        derived=True,
    ),
    StateEntry(
        id="triggers",
        kind=KIND_JSON_FILE,
        path="triggers.json",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_UNION_BY_ID,
        help="the one trigger store (automations, event triggers, hooks)",
    ),
    StateEntry(
        id="crons",
        kind=KIND_JSON_FILE,
        path="crons.json",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_UNION_BY_ID,
        help="scheduled jobs (legacy; read-only, absorbed by triggers.json)",
    ),
    StateEntry(
        id="cron_scripts",
        kind=KIND_TREE,
        path="crons",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_UNION_BY_ID,
        help="script-cron files (the scripts + configs triggers.json script jobs execute)",
    ),
    StateEntry(
        id="selfqa",
        kind=KIND_TREE,
        path="selfqa",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_LWW,
        help="self-QA companion state (the commit-watch last-seen head)",
    ),
    StateEntry(
        id="hooks",
        kind=KIND_JSON_FILE,
        path="hooks.json",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_UNION_BY_ID,
        help="lifecycle triggers",
    ),
    StateEntry(
        id="event_triggers",
        kind=KIND_JSON_FILE,
        path="event_triggers.json",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_UNION_BY_ID,
        help="event-pattern triggers",
    ),
    StateEntry(
        id="autonudge",
        kind=KIND_JSON_FILE,
        path="autonudge.json",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_LWW,
        help="auto-nudge state",
    ),
    StateEntry(
        id="cron_history",
        kind=KIND_JSONL_APPEND,
        path="cron-history",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_APPEND_DEDUP,
        help="scheduled-run history",
    ),
    StateEntry(
        id="workflows",
        kind=KIND_JSON_ENTITY_DIR,
        path="workflows",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_UNION_BY_ID,
        help="workflows and SOPs",
    ),
    StateEntry(
        id="skills",
        kind=KIND_TREE,
        path="skills",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="installed and authored skills",
        derived_within=(".skill_embeddings.json",),
    ),
    StateEntry(
        id="agents",
        kind=KIND_JSON_ENTITY_DIR,
        path="agents",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="agent definitions",
    ),
    StateEntry(
        id="prompts",
        kind=KIND_JSON_ENTITY_DIR,
        path="prompts",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="saved prompts",
    ),
    StateEntry(
        id="themes",
        kind=KIND_JSON_ENTITY_DIR,
        path="themes",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="custom colour themes saved from Settings > Design",
    ),
    StateEntry(
        id="prompt_snippets",
        kind=KIND_TREE,
        path="prompt_snippets",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="prompt snippets injected into system prompts",
    ),
    StateEntry(
        id="apps",
        kind=KIND_TREE,
        path="apps",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="installed app copies (their data/ holds real state)",
    ),
    StateEntry(
        id="extensions",
        kind=KIND_TREE,
        path="extensions",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="provider instances and per-use-case settings",
    ),
    StateEntry(
        id="entity_settings",
        kind=KIND_JSON_ENTITY_DIR,
        path="entity_settings",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="per-entity user settings",
    ),
    StateEntry(
        id="dashboard_views",
        kind=KIND_JSON_FILE,
        path="dashboard_views.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="composable-home views and their pinned artifact tiles",
    ),
    StateEntry(
        id="dashboard_tile_ledger",
        kind=KIND_TREE,
        path="dashboard_tiles",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        help="per-tile chatless-refresh ledger (freshness, cost, per-source outcomes)",
    ),
    StateEntry(
        id="models",
        kind=KIND_TREE,
        path="models",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        derived=True,
        help="downloaded local model weights (re-downloadable)",
    ),
    StateEntry(
        id="acp_adapters",
        kind=KIND_TREE,
        path="acp-adapters",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_REPLACE_ONLY,
        derived=True,
        help="installed ACP CLI adapters (re-installable)",
    ),
    StateEntry(
        id="workspace",
        kind=KIND_TREE,
        path="workspace",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="the agent workspace (memory markdown, scratch files)",
        derived_within=("knowledge",),
    ),
    StateEntry(
        id="screenshots",
        kind=KIND_TREE,
        path="screenshots",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="captured screenshots",
    ),
    StateEntry(
        id="crashes",
        kind=KIND_JSON_ENTITY_DIR,
        path="crashes",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        help="crash artifacts (rotated)",
    ),
    StateEntry(
        id="evals",
        kind=KIND_TREE,
        path="evals",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        derived_within=("studies/*/locked", "benchmarks/bakeoff"),
        help=(
            "offline eval substrate: scenario library, matrices, pinned results ledger, "
            "pre-registered studies (their hidden locked/ checks never leave this machine)"
        ),
    ),
    StateEntry(
        id="learning_db",
        kind=KIND_SQLITE,
        path="learning.db",
        domain=DOMAIN_MEMORY,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="the learning staging log and usage counters",
    ),
    StateEntry(
        id="inbox",
        kind=KIND_JSON_FILE,
        path="inbox.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="native inbox items",
    ),
    StateEntry(
        id="spend",
        kind=KIND_JSON_FILE,
        path="spend.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="per-day model spend (drives the budget caps)",
    ),
    StateEntry(
        id="project_trust",
        kind=KIND_JSON_FILE,
        path="project_trust.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="per-project Trust/Preview decisions (Preview → read-only project-script execution)",
    ),
    StateEntry(
        id="model_calls",
        kind=KIND_JSONL_APPEND,
        path="model_calls.jsonl",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        help="one line per model-call attempt",
    ),
    StateEntry(
        id="sampling_outcomes",
        kind=KIND_JSONL_APPEND,
        path="sampling_outcomes.jsonl",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        derived=True,
        help="one bounded line per best-of-N sampling call (did sampling help?)",
    ),
    StateEntry(
        id="usage_ledger",
        kind=KIND_JSONL_APPEND,
        path="usage",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        derived=True,
        help="per-turn token + cost ledger (usage/turns.jsonl)",
    ),
    StateEntry(
        id="session_search_db",
        kind=KIND_SQLITE,
        path="session_search.db",
        domain=DOMAIN_WORK,
        merge=MERGE_REPLACE_ONLY,
        derived=True,
        help="FTS index over transcripts (rebuilt by reindex_session)",
    ),
    StateEntry(
        id="codegraph",
        kind=KIND_TREE,
        path="codegraph",
        domain=DOMAIN_WORK,
        merge=MERGE_REPLACE_ONLY,
        derived=True,
        db_container=True,
        help="per-workspace symbol index (re-parsed on mtime)",
    ),
    StateEntry(
        id="workflow_runs_db",
        kind=KIND_SQLITE,
        path="workflows/runs.db",
        domain=DOMAIN_AUTOMATION,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="the workflow run ledger",
    ),
    StateEntry(
        id="knowledge_root_db",
        kind=KIND_SQLITE,
        path="knowledge/knowledge.db",
        domain=DOMAIN_KNOWLEDGE,
        merge=MERGE_SQLITE_ATTACH_IGNORE,
        help="the home-level knowledge store",
    ),
    StateEntry(
        id="agent_metadata",
        kind=KIND_JSON_ENTITY_DIR,
        path="agent-metadata",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="per-agent metadata records",
    ),
    StateEntry(
        id="learning_proposals",
        kind=KIND_TREE,
        path="learning",
        domain=DOMAIN_MEMORY,
        merge=MERGE_UNION_BY_ID,
        help="staged learning proposals awaiting review",
    ),
    StateEntry(
        id="durability_state",
        kind=KIND_JSON_FILE,
        path="durability_state.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="the durability scheduler's own last-run state",
    ),
    StateEntry(
        id="chat_plans",
        kind=KIND_JSON_ENTITY_DIR,
        path="chat_plans",
        domain=DOMAIN_WORK,
        merge=MERGE_UNION_BY_ID,
        help="saved per-chat planning state",
    ),
    StateEntry(
        id="digest_queue",
        kind=KIND_JSONL_APPEND,
        path="digest_queue.jsonl",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        help="queued notification digests",
    ),
    StateEntry(
        id="engagement",
        kind=KIND_JSON_FILE,
        path="engagement.json",
        domain=DOMAIN_MEMORY,
        merge=MERGE_LWW,
        help="learned topic engagement weights",
    ),
    StateEntry(
        id="graph_maintenance",
        kind=KIND_JSON_FILE,
        path="graph_maintenance.json",
        domain=DOMAIN_KNOWLEDGE,
        merge=MERGE_LWW,
        help="knowledge graph maintenance watermarks",
    ),
    StateEntry(
        id="inbox_spool",
        kind=KIND_TREE,
        path="inbox",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="incoming filesystem inbox items",
    ),
    StateEntry(
        id="inbox_state",
        kind=KIND_JSON_FILE,
        path="inbox_state.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="inbox read and delivery state",
    ),
    StateEntry(
        id="incident",
        kind=KIND_JSON_FILE,
        path="incident.json",
        domain=DOMAIN_SECURITY,
        merge=MERGE_LWW,
        help="security incident response state",
    ),
    StateEntry(
        id="onboarding",
        kind=KIND_TREE,
        path="onboarding",
        domain=DOMAIN_CONFIG,
        merge=MERGE_UNION_BY_ID,
        help="onboarding import state and staged records",
    ),
    StateEntry(
        id="packs",
        kind=KIND_TREE,
        path="packs",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="installed pack ledger and staged pack content",
    ),
    StateEntry(
        id="recent_projects",
        kind=KIND_JSON_FILE,
        path="recent_projects.json",
        domain=DOMAIN_WORK,
        merge=MERGE_LWW,
        help="recent project locations",
    ),
    StateEntry(
        id="research_reports",
        kind=KIND_JSON_FILE,
        path="research_reports.json",
        domain=DOMAIN_KNOWLEDGE,
        merge=MERGE_UNION_BY_ID,
        help="research report definitions and completion receipts",
    ),
    StateEntry(
        id="runners",
        kind=KIND_JSON_ENTITY_DIR,
        path="runners",
        domain=DOMAIN_CONFIG,
        merge=MERGE_UNION_BY_ID,
        help="user runner catalog",
    ),
    StateEntry(
        id="sources",
        kind=KIND_TREE,
        path="sources",
        domain=DOMAIN_KNOWLEDGE,
        merge=MERGE_UNION_BY_ID,
        help="saved source queries, streams, and digest cursors",
    ),
    StateEntry(
        id="surfaces",
        kind=KIND_TREE,
        path="surfaces",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_UNION_BY_ID,
        help="user and agent surface overlays",
    ),
    StateEntry(
        id="folders",
        kind=KIND_JSON_FILE,
        path="folders.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="folder organization",
    ),
    StateEntry(
        id="tags",
        kind=KIND_JSON_FILE,
        path="tags.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="tag vocabulary",
    ),
    StateEntry(
        id="tool_usage",
        kind=KIND_JSON_FILE,
        path="tool_usage.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="tool usage counters",
    ),
    StateEntry(
        id="tokenjuice_savings",
        kind=KIND_JSON_FILE,
        path="tokenjuice_savings.json",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_LWW,
        help="context-savings ledger",
    ),
    StateEntry(
        id="feedback",
        kind=KIND_JSONL_APPEND,
        path="feedback.jsonl",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        help="thumbs feedback on AI judgments",
    ),
    StateEntry(
        id="notifications",
        kind=KIND_JSONL_APPEND,
        path="notifications.jsonl",
        domain=DOMAIN_PLATFORM,
        merge=MERGE_APPEND_DEDUP,
        help="notification history",
    ),
    StateEntry(
        id="config",
        kind=KIND_JSON_FILE,
        path="config.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="the main configuration document",
    ),
    StateEntry(
        id="autonomy_rungs",
        kind=KIND_JSON_FILE,
        path="autonomy_rungs.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_LWW,
        help="earned-autonomy rung grants and demotion history",
    ),
    StateEntry(
        id="autonomy_reversals",
        kind=KIND_JSON_FILE,
        path="autonomy_reversals.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_LWW,
        help="undo handles for actions that ran at the auto-with-undo rung",
    ),
    StateEntry(
        id="active_models",
        kind=KIND_JSON_FILE,
        path="active_models.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="per-use-case model bindings",
    ),
    StateEntry(
        id="active_search_providers",
        kind=KIND_JSON_FILE,
        path="active_search_providers.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="search provider bindings",
    ),
    StateEntry(
        id="voice_profiles",
        kind=KIND_JSON_ENTITY_DIR,
        path="voice_profiles",
        domain=DOMAIN_CONFIG,
        merge=MERGE_UNION_BY_ID,
        tombstones=True,
        help="voice profiles: records, reference audio, locked clips, consent recordings",
        derived_within=("*/history",),
    ),
    StateEntry(
        id="voice_bindings",
        kind=KIND_JSON_FILE,
        path="voice_bindings.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="per-surface voice profile bindings (channel/agent/client + default)",
    ),
    StateEntry(
        id="active_prompts",
        kind=KIND_JSON_FILE,
        path="active_prompts.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="active prompt selections",
    ),
    StateEntry(
        id="tool_prefs",
        kind=KIND_JSON_FILE,
        path="tool_prefs.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="disabled tools and providers",
    ),
    StateEntry(
        id="mcp",
        kind=KIND_JSON_FILE,
        path="mcp.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="MCP server configuration",
    ),
    StateEntry(
        id="session_map",
        kind=KIND_JSON_FILE,
        path="session_map.json",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="provider session id map (machine-local)",
    ),
    StateEntry(
        id="project_dir",
        kind=KIND_JSON_FILE,
        path="project_dir",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="the bound project directory pointer",
    ),
    StateEntry(
        id="workspace_dir",
        kind=KIND_JSON_FILE,
        path="workspace_dir",
        domain=DOMAIN_CONFIG,
        merge=MERGE_REPLACE_ONLY,
        help="the bound workspace directory pointer",
    ),
    StateEntry(
        id="auth",
        kind=KIND_TREE,
        path="auth",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="owner credential hash and single-use authentication code stores",
    ),
    StateEntry(
        id="sel_hmac_key",
        kind=KIND_TREE,
        path="sel_hmac.key",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="audit-log HMAC key",
    ),
    StateEntry(
        id="telemetry_salt",
        kind=KIND_TREE,
        path="telemetry_salt",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="local hashing salt",
    ),
    StateEntry(
        id="local_secret",
        kind=KIND_TREE,
        path=".local_secret",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="gateway session-token secret",
    ),
    StateEntry(
        id="env",
        kind=KIND_TREE,
        path=".env",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="provider credentials",
    ),
    StateEntry(
        id="env_pre_keychain",
        kind=KIND_TREE,
        path=".env.pre-keychain",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="pre-migration .env snapshot (rollback source for the keychain move)",
    ),
    StateEntry(
        id="credentials",
        kind=KIND_TREE,
        path="credentials",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="the credential store",
    ),
    StateEntry(
        id="provider_credentials",
        kind=KIND_JSON_FILE,
        path="credentials.json",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        help="provider API keys",
    ),
    StateEntry(
        id="huggingface_auth_cache",
        kind=KIND_JSON_FILE,
        path="huggingface_auth_cache.json",
        domain=DOMAIN_SECURITY,
        merge=MERGE_REPLACE_ONLY,
        secret=True,
        derived=True,
        help="derived Hugging Face token-validation status (never the token)",
    ),
    StateEntry(
        id="security_events",
        kind=KIND_JSONL_APPEND,
        path="security_events.jsonl",
        domain=DOMAIN_SECURITY,
        merge=MERGE_APPEND_DEDUP,
        help="the security event log (audit trail)",
    ),
    StateEntry(
        id="inbound_clients",
        kind=KIND_JSON_FILE,
        path="inbound_clients.json",
        domain=DOMAIN_SECURITY,
        merge=MERGE_LWW,
        help="inbound access clients: labels, bindings and token hashes (never tokens)",
    ),
    StateEntry(
        id="inbound_audit",
        kind=KIND_JSONL_APPEND,
        path="inbound_audit.jsonl",
        domain=DOMAIN_SECURITY,
        merge=MERGE_APPEND_DEDUP,
        derived=True,
        help="per-request inbound trace (local; security events also go to the SEL)",
    ),
)


IGNORED: tuple[str, ...] = (
    "desktop-private",
    "snapshots",
    "outbox",
    "sync",
    "shards",
    "state-history",
    "locks",
    "__pycache__",
    "*.log",
    "*.log.*",
    "*.pid",
    "*.lock",
    "*.bak",
    "*-wal",
    "*-shm",
    "*.tmp",
    ".DS_Store",
    "session_pids.txt",
    "session_pids.lock",
    "agent_pids.txt",
    "doctor",
    ".git",
    "session_key",
    "sessions.json",
    "machine_id",
    "browse",
    "update_check.json",
    "update_releases.json",
    "fixture.yaml",
    "gateway.runtime.json",
)


def all_entries() -> tuple[StateEntry, ...]:
    return INVENTORY


def by_id(entry_id: str) -> StateEntry | None:
    return next((e for e in INVENTORY if e.id == entry_id), None)


def domains() -> tuple[str, ...]:
    """The domains actually present in the manifest, in declaration order."""
    seen: list[str] = []
    for entry in INVENTORY:
        if entry.domain not in seen:
            seen.append(entry.domain)
    return tuple(seen)


def entries_for_domain(domain: str) -> tuple[StateEntry, ...]:
    return tuple(e for e in INVENTORY if e.domain == domain)


def backup_entries(*, include_derived: bool = False) -> tuple[StateEntry, ...]:
    """Entries a SNAPSHOT should capture. Secrets are included (a snapshot is a
    local, 0600 archive — losing the credential store is exactly what a backup
    should prevent); derived indexes are skipped unless asked for, since they
    rebuild and a stale index paired with a newer store is worse than none."""
    return tuple(e for e in INVENTORY if include_derived or not e.derived)


def export_entries() -> tuple[StateEntry, ...]:
    """Entries a PORTABLE EXPORT may contain — the projection that replaces
    `portability.EXPORT_EXCLUDE`: neither secrets (they must never leave the
    machine) nor derived data (rebuildable)."""
    return tuple(e for e in INVENTORY if not e.secret and not e.derived)


def secret_paths() -> tuple[str, ...]:
    return tuple(e.path for e in INVENTORY if e.secret)


def sqlite_entries() -> tuple[StateEntry, ...]:
    """Every database in the manifest. Callers MUST copy these with the sqlite
    backup API rather than a filesystem copy: the gateway holds them open in WAL
    mode, so a raw copy can capture a torn page set. This projection is what
    fixed the live `knowledge.db` raw-copy hazard — it was outside the old
    hand-written allowlist and got tree-copied."""
    return tuple(e for e in INVENTORY if e.kind == KIND_SQLITE)


def _parts(rel: str) -> tuple[str, ...]:
    """Home-relative path split into its meaningful segments."""
    return tuple(p for p in rel.replace("\\", "/").split("/") if p and p != ".")


def is_ignored(rel: str) -> bool:
    """Whether a home-relative path is deliberately not state."""
    parts = _parts(rel)
    for pattern in IGNORED:
        for part in parts:
            if fnmatch.fnmatch(part, pattern):
                return True
    return False


def claim_for(rel: str) -> StateEntry | None:
    """The entry claiming a home-relative path, or None.

    Longest path match wins, so a nested store claims its own subtree even when
    an ancestor entry also exists (``workspace/knowledge/knowledge.db`` belongs
    to ``knowledge_db``, not to ``workspace``).
    """
    parts = _parts(rel)
    best: StateEntry | None = None
    best_depth = -1
    for entry in INVENTORY:
        ep = _parts(entry.path)
        if (
            len(ep) <= len(parts)
            and tuple(parts[: len(ep)]) == ep
            and len(ep) > best_depth
        ):
            best, best_depth = entry, len(ep)
    return best


@dataclass
class AuditResult:
    """What :func:`audit_home` found."""

    unclaimed: list[str] = field(default_factory=list)
    claimed: int = 0
    ignored: int = 0
    undeclared_dbs: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unclaimed and not self.undeclared_dbs


def audit_home(home: Path) -> AuditResult:
    """Assert every top-level path under ``home`` is claimed or ignored.

    This is the guard that keeps the manifest honest: when a new store directory
    appears and nobody declared it, this reports it and the test that calls it
    fails — which is precisely how nine directories silently escaped backup
    before the inventory existed.

    Scans the top level plus one level inside each unclaimed directory, which is
    enough to name the offending store without walking a huge tree.
    """
    result = AuditResult()
    if not home.is_dir():
        return result
    for child in sorted(home.iterdir()):
        rel = child.name
        if is_ignored(rel):
            result.ignored += 1
            continue
        if claim_for(rel) is not None:
            result.claimed += 1
            continue
        if child.is_dir() and any(e.path.startswith(rel + "/") for e in INVENTORY):
            result.claimed += 1
            continue
        result.unclaimed.append(rel + ("/" if child.is_dir() else ""))

    declared = {e.path for e in sqlite_entries()}
    declared_trees = tuple(e.path + "/" for e in INVENTORY if e.db_container)
    for db in sorted(set(home.rglob("*.db")) | set(home.rglob("*.sqlite3"))):
        rel_db = db.relative_to(home).as_posix()
        if is_ignored(rel_db) or rel_db in declared:
            continue
        if rel_db.startswith(declared_trees):
            continue
        result.undeclared_dbs.append(rel_db)
    return result
