"""Run workspace + environment: the provisioning block, folder contracts, env grants (S49).

A run that touches the filesystem needs to say WHERE, and the answer has to be a declaration rather
than a convention — Gideon has already been bitten by the alternative (the deleted-real-model
incident, where an in-place run's destructive step ran against real state).

Three things land, each deliberately built on machinery that already exists:

* **The `workspace` block** — `{mode, preserve_patterns, setup, teardown, env}`. Worktree mode
  reuses `loop/worktree.py` (`.worktrees/<id>` + `gideon/task-*` branches), which is proven,
  rather than a second git implementation that would drift from it.
* **Folder contracts** (`.folder.yaml`) — validated as WARNINGS, never fatal, and unknown
  fields pass silently. That tolerance is the lesson of the 23-of-25-dropped-memories bug:
  a strict reader over a format that evolves discards the data it was meant to protect.
* **Per-project env grants** — the spawn env for a leaf is SECRET-FILTERED: only explicitly
granted
  keys reach a child. It extends the existing credential seam (`{{secret:KEY}}` + the credential
  store) rather than adding a second secret mechanism, and every surface shows presence flags only.

The asymmetries worth stating up front, because each fails in a chosen direction:

* **Setup failure does not block the run**; teardown failure does not block deletion. Setup is
  best-effort convenience — refusing to run because `npm install` failed would make the block a
  liability. But teardown runs BEFORE deletion, always, because its whole job is to stop
  services and
  sync artifacts out while the workspace still exists.
* **A reserved env var is REJECTED, not overridden.** Letting a run set `HOME` or `PATH`
is letting it
  redirect every subsequent tool invocation, including the ones that enforce the other rules here.
* **An ungranted secret is absent, not empty.** An empty string reads to a child as "this credential
  is configured and blank", which produces an authentication error instead of a missing-
  config error.

Pure functions over declarations. Provisioning I/O stays with the caller; this module decides.
"""

from __future__ import annotations

import fcntl
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

RESERVED_ENV_VARS = frozenset(
    {
        "HOME",
        "PATH",
        "SHELL",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "PYTHONPATH",
        "PYTHONHOME",
    }
)

RESERVED_ENV_PREFIXES = ("XDG_", "GIDEON_", "DYLD_", "LD_")


@contextmanager
def worktree_registration_lock(workspace: str) -> Iterator[None]:
    """Serialize git worktree registration for one repository.

    ``git worktree add`` mutates the repository's shared worktree registry, so
    independent task workers cannot safely run that command concurrently.  A
    blocking file lock queues both threads and processes while leaving checkout
    and hydration outside the critical section.
    """
    from gideon.core.concurrency import lock_path

    repository = str(Path(workspace).expanduser().resolve())
    with lock_path(f"git-worktree-registration:{repository}").open("a+") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lease, fcntl.LOCK_UN)


class Lifecycle(str, Enum):
    TRANSIENT = "transient"
    TTL_STAGING = "ttl_staging"
    PERMANENT = "permanent"
    IMMUTABLE = "immutable"


TTL_STAGING_DAYS = 14


class Mode(str, Enum):
    """Where a run's filesystem work happens.

    `IN_PLACE` is the dangerous one and is never a default: it is the mode in which a
    destructive step
    runs against real state. `SCRATCH` is the default precisely because being wrong about isolation
    should cost a copy, not the original.
    """

    SCRATCH = "scratch"
    WORKTREE = "worktree"
    IN_PLACE = "in_place"
    CONTAINER = "container"


ISOLATED_MODES = frozenset({Mode.SCRATCH, Mode.WORKTREE, Mode.CONTAINER})


@dataclass
class WorkspaceSpec:
    """A run's declared workspace.

    `preserve_patterns` is the adoption-critical detail: a worktree with no `.env` is a
    worktree where
    every build fails, and a user whose first isolated run cannot install dependencies concludes
    isolation is broken rather than unconfigured.
    """

    mode: Mode = Mode.SCRATCH
    preserve_patterns: list[str] = field(default_factory=list)
    setup: str = ""
    teardown: str = ""
    env: dict[str, str | None] = field(default_factory=dict)
    name: str = ""
    container: dict[str, Any] = field(default_factory=dict)

    @property
    def isolated(self) -> bool:
        return self.mode in ISOLATED_MODES

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "preserve_patterns": list(self.preserve_patterns),
            "setup": self.setup,
            "teardown": self.teardown,
            "env": {key: (value is not None) for key, value in self.env.items()},
            "name": self.name,
            "container": dict(self.container),
            "isolated": self.isolated,
        }


@dataclass
class SpecIssue:
    """One problem with a declaration. `fatal` refuses provisioning; otherwise it is advisory."""

    code: str
    message: str
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "fatal": self.fatal}


_GREEDY_PATTERNS = ("**", "**/*", "/", ".", "./", "*")


def parse_workspace(raw: Any) -> tuple[WorkspaceSpec, list[SpecIssue]]:
    return _WorkspaceReader(raw).read()


def parse_env(raw: Any) -> tuple[dict[str, str | None], list[SpecIssue]]:
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [
            SpecIssue("env_not_object", "workspace.env must be an object", fatal=True)
        ]
    accepted: dict[str, str | None] = {}
    issues: list[SpecIssue] = []
    for key, value in raw.items():
        name = str(key)
        problem = _EnvironmentField.admission(name)
        if problem is None:
            accepted[name] = None if value is None else str(value)
        else:
            issues.append(problem)
    return accepted, issues


def is_valid_env_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""))


def is_reserved_env(name: str) -> bool:
    upper = (name or "").upper()
    return upper in RESERVED_ENV_VARS or upper.startswith(RESERVED_ENV_PREFIXES)


def spawn_env(
    spec: WorkspaceSpec,
    *,
    granted: dict[str, str] | None = None,
    host_env: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    grants, host = granted or {}, host_env or {}
    delivered: dict[str, str] = {}
    withheld: list[str] = []
    for name, value in spec.env.items():
        present, resolved = _EnvironmentField(name, value).resolve(grants, host)
        if present:
            delivered[name] = resolved
        else:
            withheld.append(name)
    return delivered, withheld


def looks_secret(name: str) -> bool:
    """Whether an env var name looks credential-bearing."""
    from gideon.automation.workflows.secrets import matches_secret_hint

    return matches_secret_hint(name)


def presence_flags(
    spec: WorkspaceSpec, granted: dict[str, str] | None = None
) -> dict[str, Any]:
    grants = granted or {}
    states = {
        name: _EnvironmentField(name, value).presence(grants)
        for name, value in spec.env.items()
    }
    return {"env": states, "count": len(states)}


SETUP_MARKER_DIR = ".gideon-setup"


def setup_marker(step: str) -> str:
    """The marker path for one setup step. Content-addressed by the step text, so editing the step
    re-runs it — a marker keyed by index would skip an edited step as though it had run.
    """
    import hashlib

    digest = hashlib.sha256((step or "").encode("utf-8")).hexdigest()[:16]
    return f"{SETUP_MARKER_DIR}/{digest}.done"


@dataclass
class Provisioning:
    """The ordered plan for standing a workspace up, and for tearing it down.

    Order is the contract: preserve → setup → run, and teardown → delete. `teardown` before
    deletion
    is the whole reason it exists — running it after would be running it against a directory
    that no
    longer holds the services or artifacts it was meant to stop and sync.
    """

    steps: list[str] = field(default_factory=list)
    teardown_steps: list[str] = field(default_factory=list)
    issues: list[SpecIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.fatal for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": list(self.steps),
            "teardown_steps": list(self.teardown_steps),
            "issues": [i.to_dict() for i in self.issues],
            "ok": self.ok,
        }


def plan_provisioning(
    spec: WorkspaceSpec, *, issues: list[SpecIssue] | None = None
) -> Provisioning:
    plan = Provisioning(issues=list(issues or []))
    schedule = _WorkspacePlan(spec)
    plan.steps.extend(schedule.arrival())
    plan.teardown_steps.extend(schedule.departure())
    return plan


@dataclass
class FolderContract:
    """A `.folder.yaml` declaration.

    Every field is optional and unknown fields pass silently. That tolerance is the point:
    this reads
    a format that will grow, and a strict reader over an evolving format discards the data it was
    meant to protect — the 23-of-25-dropped-memories bug class.
    """

    role: str = ""
    lifecycle: Lifecycle = Lifecycle.TRANSIENT
    agent_writable: bool = False
    required_frontmatter: list[str] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    unknown: dict[str, Any] = field(default_factory=dict)

    @property
    def ttl_days(self) -> int:
        return TTL_STAGING_DAYS if self.lifecycle is Lifecycle.TTL_STAGING else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "lifecycle": self.lifecycle.value,
            "agent_writable": self.agent_writable,
            "required_frontmatter": list(self.required_frontmatter),
            "defaults": dict(self.defaults),
            "ttl_days": self.ttl_days,
            **self.unknown,
        }


_KNOWN_CONTRACT_FIELDS = frozenset(
    {"role", "lifecycle", "agent_writable", "required_frontmatter", "defaults"}
)


def parse_folder_contract(raw: Any) -> tuple[FolderContract, list[SpecIssue]]:
    if isinstance(raw, dict):
        return _FolderReader(raw).read()
    return FolderContract(), [
        SpecIssue(
            "contract_not_object", "folder contract must be an object; using defaults"
        )
    ]


def may_write(contract: FolderContract) -> tuple[bool, str]:
    """Whether an agent may write here, and why not when it may not.

    Immutable refuses; everything else honors `agent_writable`, which DEFAULTS to False.
    Defaulting to
    writable would make every folder that forgot to declare a permission an open one, and the
    forgetting is the common case.
    """
    if contract.lifecycle is Lifecycle.IMMUTABLE:
        return (
            False,
            "folder is immutable (ingested reference material or a file-drop landing zone)",
        )
    if not contract.agent_writable:
        return False, "folder does not declare agent_writable"
    return True, ""


def validate_frontmatter(
    contract: FolderContract, frontmatter: dict[str, Any]
) -> list[SpecIssue]:
    present = frontmatter or {}
    missing = sorted(
        field for field in contract.required_frontmatter if field not in present
    )
    return (
        [
            SpecIssue(
                "missing_frontmatter",
                f"missing required frontmatter: {', '.join(missing)} — recorded, not refused",
            )
        ]
        if missing
        else []
    )


class _WorkspaceReader:
    def __init__(self, declaration: Any):
        self.raw = declaration
        self.issues: list[SpecIssue] = []

    def read(self) -> tuple[WorkspaceSpec, list[SpecIssue]]:
        if self.raw is None:
            return WorkspaceSpec(), self.issues
        if not isinstance(self.raw, dict):
            self.issues.append(
                SpecIssue(
                    "workspace_not_object", "workspace must be an object", fatal=True
                )
            )
            return WorkspaceSpec(), self.issues
        selected = (
            str(self.raw.get("mode", Mode.SCRATCH.value) or Mode.SCRATCH.value)
            .strip()
            .lower()
        )
        try:
            mode = Mode(selected)
        except ValueError:
            self.issues.append(
                SpecIssue(
                    "unknown_mode",
                    f"unknown workspace mode {selected!r}; expected one of "
                    f"{sorted(m.value for m in Mode)} — defaulting would run in a mode nobody chose, "
                    "and `in_place` touches the real tree",
                    fatal=True,
                )
            )
            return WorkspaceSpec(), self.issues
        patterns = self.patterns()
        environment, problems = parse_env(self.raw.get("env"))
        self.issues.extend(problems)
        container = self.container(mode)
        spec = WorkspaceSpec(
            mode=mode,
            preserve_patterns=patterns,
            setup=str(self.raw.get("setup", "") or "").strip(),
            teardown=str(self.raw.get("teardown", "") or "").strip(),
            env=environment,
            name=str(self.raw.get("name", "") or "").strip(),
            container=container,
        )
        if mode is Mode.IN_PLACE and spec.teardown:
            self.issues.append(
                SpecIssue(
                    "in_place_teardown",
                    "teardown on an in_place workspace runs against the user's real tree — a cleanup "
                    "command here deletes real work, not scratch state",
                )
            )
        return spec, self.issues

    def patterns(self) -> list[str]:
        selected = [
            str(value)
            for value in (self.raw.get("preserve_patterns") or [])
            if str(value).strip()
        ]
        self.issues.extend(
            SpecIssue(
                "greedy_preserve_pattern",
                f"preserve pattern {value!r} would copy the whole tree into an isolated "
                "workspace, which defeats the isolation it is being copied into",
                fatal=True,
            )
            for value in selected
            if value.strip() in _GREEDY_PATTERNS
        )
        return selected

    def container(self, mode: Mode) -> dict[str, Any]:
        declaration = self.raw.get("container")
        carried = dict(declaration) if isinstance(declaration, dict) else {}
        if mode is not Mode.CONTAINER:
            if carried:
                self.issues.append(
                    SpecIssue(
                        "container_block_ignored",
                        f"a container manifest is declared but the mode is {mode.value!r} — it only "
                        "takes effect with `mode: container`",
                    )
                )
            return carried
        from gideon.automation.workflows.container_env import parse_manifest

        if declaration is not None:
            _, problems = parse_manifest(declaration)
            self.issues.extend(problems)
        else:
            self.issues.append(
                SpecIssue(
                    "container_no_manifest",
                    "mode is `container` but no `container:` manifest is declared — the run "
                    "will use an isolated scratch dir until an image or build is given",
                )
            )
        return carried


class _EnvironmentField:
    def __init__(self, name: str, declaration: str | None):
        self.name = name
        self.declaration = declaration
        self.inherited = declaration is None
        self.binding = declaration is not None and declaration.startswith("{{secret:")
        self.key = (
            declaration[len("{{secret:") :].rstrip("}").strip()
            if self.binding
            else name
        )

    @staticmethod
    def admission(name: str) -> SpecIssue | None:
        if not is_valid_env_name(name):
            return SpecIssue(
                "invalid_env_name", f"{name!r} is not a valid environment variable name"
            )
        if is_reserved_env(name):
            return SpecIssue(
                "reserved_env",
                f"{name} is reserved and cannot be overridden — redirecting it relocates every "
                "config file, credential store or binary the system resolves through it",
                fatal=True,
            )
        return None

    def resolve(self, grants: dict[str, str], host: dict[str, str]) -> tuple[bool, str]:
        if not self.inherited and not self.binding:
            return True, self.declaration
        if self.key in grants:
            return True, grants[self.key]
        if self.inherited and self.name in host and not looks_secret(self.name):
            return True, host[self.name]
        return False, ""

    def presence(self, grants: dict[str, str]) -> str:
        if self.inherited:
            return "inherited"
        if not self.binding:
            return "literal"
        return "granted" if self.key in grants else "declared_not_granted"


class _WorkspacePlan:
    def __init__(self, spec: WorkspaceSpec):
        self.spec = spec

    def arrival(self):
        entries = (
            (Mode.IN_PLACE, "use the project workspace in place (no isolation)"),
            (Mode.WORKTREE, "create a git worktree via loop.worktree.add_worktree"),
            (Mode.CONTAINER, "provision the declared container image"),
        )
        yield next(
            (text for mode, text in entries if self.spec.mode is mode),
            "create a per-run scratch directory",
        )
        yield from (f"copy in {pattern}" for pattern in self.spec.preserve_patterns)
        if self.spec.setup:
            yield f"run setup (guarded by {SETUP_MARKER_DIR}/ markers): {self.spec.setup}"

    def departure(self):
        if self.spec.teardown:
            yield f"run teardown BEFORE deletion: {self.spec.teardown}"
        if self.spec.isolated:
            yield "commit outstanding work to a per-run branch"
            yield "delete the workspace"


class _FolderReader:
    def __init__(self, declaration: dict):
        self.raw = declaration
        self.issues: list[SpecIssue] = []

    def lifecycle(self) -> Lifecycle:
        name = str(self.raw.get("lifecycle", "") or "").strip().lower()
        if not name:
            return Lifecycle.TRANSIENT
        try:
            return Lifecycle(name)
        except ValueError:
            self.issues.append(
                SpecIssue(
                    "unknown_lifecycle",
                    f"unknown lifecycle {name!r}; treating as transient, which is the "
                    "recoverable direction to be wrong in",
                )
            )
            return Lifecycle.TRANSIENT

    def read(self) -> tuple[FolderContract, list[SpecIssue]]:
        lifecycle = self.lifecycle()
        extensions = {
            key: value
            for key, value in self.raw.items()
            if key not in _KNOWN_CONTRACT_FIELDS
        }
        contract = FolderContract(
            role=str(self.raw.get("role", "") or ""),
            lifecycle=lifecycle,
            agent_writable=bool(self.raw.get("agent_writable", False)),
            required_frontmatter=list(
                map(str, self.raw.get("required_frontmatter") or [])
            ),
            defaults=dict(self.raw.get("defaults") or {}),
            unknown=extensions,
        )
        if lifecycle is Lifecycle.IMMUTABLE and contract.agent_writable:
            self.issues.append(
                SpecIssue(
                    "immutable_but_writable",
                    "an immutable folder declaring agent_writable contradicts itself; treating as "
                    "NOT writable, because the immutable declaration is the one with a safety purpose",
                )
            )
            contract.agent_writable = False
        return contract, self.issues
