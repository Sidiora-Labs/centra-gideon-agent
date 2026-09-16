"""Self-QA Companion core (SELF-VERIFICATION §3) — the commit→triage→scenario→findings loop.

The composed loop is **commit → triage → as-a-user execution → evidence → findings**, and
every primitive it needs already exists: the zero-token cron seam
(:mod:`gideon.automation.schedule_script`), the bundled template pack
(:mod:`gideon.automation.workflows.bundled_defs`), the run ledger
(:mod:`gideon.assurance.ledger`), the native inbox push sink
(:func:`gideon.integrations.inbox_providers.native_source.post_to_inbox`) and the native task
provider (:mod:`gideon.engine.tasks.registry`). This package is the composition plus the
genuinely new pieces: **triage** (which commits are worth a scenario), **filing** (what a
failure leaves behind), **evidence** (the SHA256'd bundle a failure carries), and the optional
**fix branch** a confirmed finding opens.

Four deliberate shapes:

**Triage is deterministic, not inferred.** A commit's user-impact is decided from its changed
paths (:mod:`gideon.assurance.selfqa.triage`), not from a model call. The plan sketched an `infer`
node; a path classifier is cheaper, is the same answer every time, and — the reason that
matters here — is *assertable*. A prompt-only triage makes "a test-only commit skipped for the
right reason" indistinguishable from "the companion never fired", which is the exact failure
this loop exists to catch. The template still carries the deep-as-a-user *scenario* prompt,
where judgment genuinely is needed.

**A skip writes a row.** `impact=test` and `impact=none` do not silently drop the commit: they
write one ledger record carrying the one-line rationale
(:func:`gideon.assurance.selfqa.ledger.record_triage`). Silence and a correct skip must not look
alike from the run inbox.

**Filing is exactly one of each.** A failing scenario files one Inbox item and one Task
(:func:`gideon.assurance.selfqa.findings.file_finding`) — one is both floor and ceiling, and the
function enforces both ends rather than trusting its caller to call it once.

**Evidence is measured, not claimed.** A failing scenario's bundle — screenshots, recording,
contact-sheet, GIF, logs — is indexed by a SHA256'd manifest the code computes
(:mod:`gideon.assurance.selfqa.evidence`) and registered as a single Artifact, and the completion
gate refuses a run whose bundle is missing a required kind. The contact-sheet and GIF come from
ffmpeg as a local subprocess that degrades typed when ffmpeg is absent, never crashes.

Nothing here writes to `memory.db` or `knowledge.db`: commit-watch state is a file under the
cron-scripts dir, findings are Inbox/Task entities, evidence is an Artifact.
"""

from __future__ import annotations

from gideon.assurance.selfqa.evidence import (
    DEFAULT_REQUIRED_KINDS,
    KIND_CONTACT_SHEET,
    KIND_GIF,
    KIND_LOG,
    KIND_MANIFEST,
    KIND_RECORDING,
    KIND_SCREENSHOT,
    Derivation,
    GateResult,
    Manifest,
    ManifestEntry,
    RegisteredBundle,
    build_manifest,
    check_required_kinds,
    derive_contact_sheet,
    derive_gif,
    ffmpeg_available,
    register_bundle,
    write_manifest,
)
from gideon.assurance.selfqa.findings import FiledFinding, ScenarioFinding, file_finding
from gideon.assurance.selfqa.fix_branch import (
    BRANCH_PREFIX,
    FixBranchResult,
    create_fix_branch,
    fix_branch_name,
)
from gideon.assurance.selfqa.install import (
    WATCH_TRIGGER_ID,
    reconcile,
    remove_retired_script,
)
from gideon.assurance.selfqa.ledger import record_triage
from gideon.assurance.selfqa.triage import (
    IMPACT_NONE,
    IMPACT_TEST,
    IMPACT_USER,
    SKIPPED_IMPACTS,
    CommitTriage,
    classify_paths,
    triage_commit,
    triage_commits,
)
from gideon.assurance.selfqa.watch import WatchFire, check

__all__ = [
    "CommitTriage",
    "FiledFinding",
    "ScenarioFinding",
    "WATCH_TRIGGER_ID",
    "WatchFire",
    "IMPACT_NONE",
    "IMPACT_TEST",
    "IMPACT_USER",
    "SKIPPED_IMPACTS",
    "check",
    "classify_paths",
    "file_finding",
    "reconcile",
    "record_triage",
    "remove_retired_script",
    "triage_commit",
    "triage_commits",
    "DEFAULT_REQUIRED_KINDS",
    "KIND_CONTACT_SHEET",
    "KIND_GIF",
    "KIND_LOG",
    "KIND_MANIFEST",
    "KIND_RECORDING",
    "KIND_SCREENSHOT",
    "Derivation",
    "GateResult",
    "Manifest",
    "ManifestEntry",
    "RegisteredBundle",
    "build_manifest",
    "check_required_kinds",
    "derive_contact_sheet",
    "derive_gif",
    "ffmpeg_available",
    "register_bundle",
    "write_manifest",
    "BRANCH_PREFIX",
    "FixBranchResult",
    "create_fix_branch",
    "fix_branch_name",
]
