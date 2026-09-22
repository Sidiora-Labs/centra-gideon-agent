"""Configuration records, resource locations and the public configuration facade.

Wire policies live in decoding; document I/O preserves application-owned sections.
"""

import json
import logging
import os
import re as _re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from gideon.core.config.coercion import (
    _expose_flag,
    _guard_flag,
    _meta,
    _num,
    _safe_choice,
    _safe_float,
    _safe_int,
    _str_list,
)
from gideon.core.config.external_access import (
    CaptureSurfaceConfig,
    ExternalAccessConfig,
    ExternalAccessSurfaceConfig,
    _capture_retention,
    _ea_surface_data,
)
from gideon.core.config.learning import (
    EvalsConfig,
    FeedbackConfig,
    LearningConfig,
    LoopsConfig,
    PlanningConfig,
    ProactiveConfig,
    _identity_report_cadence,
    _judge_axis,
    _stagnation_window,
)
from gideon.core.config.safety import (
    AuthConfigSection,
    AutonomyConfig,
    BreakerConfig,
    BudgetConfig,
    EgressConfig,
    GuardrailsConfig,
    SandboxConfig,
    SecurityConfig,
)
from gideon.core.config.validation import _validate_config_data
from gideon.integrations.voice.duplex import (
    DEFAULT_CONFIRMATION_PHRASES,
    DEFAULT_EXIT_PHRASES,
    DEFAULT_PUSH_TO_TALK_CHORD,
)

logger = logging.getLogger(__name__)

CONFIG_DIR_NAME = ".gideon"

CRED_SLACK_APP_TOKEN = "SLACK_APP_TOKEN"
CRED_SLACK_BOT_TOKEN = "SLACK_BOT_TOKEN"
CRED_OWNER_ID = "GIDEON_OWNER_ID"
_CREDENTIAL_KEYS = (CRED_SLACK_APP_TOKEN, CRED_SLACK_BOT_TOKEN, CRED_OWNER_ID)

DEFAULT_SESSION_TIMEOUT = 3600

_DEFAULT_PORT = 10000

DASHBOARD_PORT: int = int(os.environ.get("GIDEON_PORT", _DEFAULT_PORT))


_WORKSPACE_DIR_NAME = "gideon-workspace"


def _workspace_dir_file() -> Path:
    return config_dir().joinpath("workspace_dir")


def _default_workspace_base() -> Path:
    return Path.home().joinpath("workplace")


def workspace_root() -> Path:
    from gideon.core.config.locations import WorkspaceLocator

    return WorkspaceLocator(
        os.environ.get("GIDEON_WORKSPACE"),
        _workspace_dir_file,
        lambda: _default_workspace_base() / _WORKSPACE_DIR_NAME,
    ).resolve()


def _surface_mode_default(value: object) -> str:
    return _safe_choice(value or "", ("off", "passive", "suggest"), "off")


def _workspace_default_mode(value: object) -> str:
    return _safe_choice(
        value or "", ("scratch", "worktree", "in_place", "container"), "scratch"
    )


def _compose_voice(voice: str, system_prompt: str) -> str:
    persona, operating = (voice or "").strip(), system_prompt or ""
    if not persona:
        return operating
    rendered = None
    try:
        from gideon.integrations.prompt_providers.runtime import render_snippet_block

        rendered = render_snippet_block(
            "agent-voice-layer", {"voice": persona, "system_prompt": operating}
        )
    except Exception:
        pass
    fallback = "\n".join(
        ("[VOICE — speak and decide as this persona]", persona, "", operating)
    )
    return (rendered or fallback).rstrip()


OUTBOX_DIR_NAME = "outbox"


def outbox_dir() -> Path:
    from gideon.core.config.locations import WorkspaceLocator

    return WorkspaceLocator.create(workspace_root() / OUTBOX_DIR_NAME)


_ensured_dirs: set[str] = set()


def _ensure_dir(p: Path) -> Path:
    key = str(p)
    if key in _ensured_dirs:
        return p
    p.mkdir(parents=True, exist_ok=True)
    _ensured_dirs.add(key)
    return p


def config_dir() -> Path:
    from gideon.core.config.locations import configuration_home

    return _ensure_dir(
        configuration_home(
            os.environ.get("GIDEON_HOME"), Path.home() / CONFIG_DIR_NAME, logger
        )
    )


def config_path() -> Path:
    return config_dir().joinpath("config.json")


_MEMORY_ROOT_DIR_NAME = "workspace"


def _slug_cwd(cwd: str) -> str:
    from gideon.core.config.locations import directory_partition

    return directory_partition(cwd)


def memory_dir_for_cwd(cwd: str | None = None) -> Path:
    partition = _slug_cwd(cwd) if cwd else "_default"
    return config_dir().joinpath(_MEMORY_ROOT_DIR_NAME, "_ext", partition)


def default_workspace_dir() -> str:
    from gideon.security.security import is_sensitive_path

    try:
        candidate = os.path.realpath(str(workspace_root()))
        permitted = os.path.isdir(candidate) and not is_sensitive_path(candidate)
        return candidate if permitted else ""
    except Exception:
        return ""


def env_path() -> Path:
    return config_dir().joinpath(".env")


def resolve_agent_config_path() -> Path:
    project = os.environ.get("GIDEON_PROJECT_DIR")
    candidates = [Path(__file__).resolve().parent / "defaults.json"]
    if project:
        candidates.insert(0, Path(project) / "agents" / "defaults.json")
    return next(
        (candidate for candidate in candidates[:-1] if candidate.exists()),
        candidates[-1],
    )


def _slug_username(value: object) -> str:
    from contextlib import suppress

    with suppress(Exception):
        from gideon.cognition.identity import slugify_username

        return slugify_username(str(value or ""))
    return ""


def _voice_phrases(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    phrases = (item.strip() for item in value if isinstance(item, str))
    return list(filter(None, phrases))


MEMORY_VAULT_MODES = ("off", "mirror", "two_way")


def _vault_mode(memory_data: dict) -> str:
    requested = str(memory_data.get("vault_mode", "") or "").strip().lower()
    legacy = "mirror" if memory_data.get("vault_enabled", False) else "off"
    return next((mode for mode in MEMORY_VAULT_MODES if mode == requested), legacy)


_BOT_NAME_MAX = 50
_BOT_NAME_RE = _re.compile(r"[^a-zA-Z0-9 _\-.]")


def _sanitize_bot_name(raw: str) -> str:
    prepared = raw.strip()[:_BOT_NAME_MAX] if isinstance(raw, str) else ""
    return _BOT_NAME_RE.sub("", prepared)


@dataclass
class SelfQaConfig:
    """Self-QA Companion settings (SELF-VERIFICATION §3) — the commit-watch QA loop.

    The companion watches a repo, triages each new commit for user-visible impact, drives one
    deep as-a-user scenario against the live gateway UI, and files a finding when the scenario
    fails. It spends model calls and drives real UI, so it is **off by default** — enabling it is
    a decision about what runs on your machine unattended, not a preference.

    ``fix_branch_enabled`` is separately off because it is a second, larger step: a confirmed
    finding spawns a coder subagent on a `gideon/selfqa-<sha8>` branch. That branch is never
    merged and never pushed, but it is still code written without a human in the loop.
    """

    enabled: bool = field(
        default=False,
        metadata=_meta(
            "Self-QA companion",
            "Watch a repo and run an as-a-user QA scenario on each user-impacting commit, "
            "filing an Inbox item and a Task when one fails. Off by default — it spends model "
            "calls and drives your browser unattended.",
        ),
    )
    watched_repo: str = field(
        default="",
        metadata=_meta(
            "Watched repository",
            "Absolute path to the git repository whose commits trigger a QA run. Empty means "
            "the watcher stays idle even when the companion is enabled.",
        ),
    )
    fix_branch_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Propose fix branches",
            "On a confirmed finding, open a `gideon/selfqa-<sha>` branch with a proposed diff. "
            "Never merged and never pushed — the branch name lands in the Task for you to "
            "review. Off by default.",
        ),
    )
    max_scenarios_per_fire: int = field(
        default=3,
        metadata=_meta(
            "Max scenarios per run",
            "Ceiling on scenarios generated from one watcher fire. A push of twenty commits "
            "should not open twenty browser sessions; the most impactful few are the ones "
            "worth checking.",
        ),
    )


@dataclass
class AgentConfig:
    approval_mode: str = field(
        default="auto",
        metadata=_meta(
            "Approval Mode",
            "Tool approval mode. 'trust_reads' auto-approves read-only tools "
            "and asks for everything else.",
            enum=["auto", "interactive", "trust_reads"],
        ),
    )
    provider: str = field(
        default="native",
        metadata=_meta(
            "Provider",
            "Default agent runtime backend for agents that don't set their own: "
            "'native' (in-process loop, governed by Settings → Models), 'acp' "
            "(external CLI), or 'acp:<cli>' to pin a specific connected runtime "
            "(e.g. 'acp:claude-code'). Per-agent 'provider' overrides this. Not a "
            "closed enum — the acp:<cli> space is open over connected runtimes, "
            "mirroring the per-agent AgentProfile.provider field.",
        ),
    )
    sandbox: str = field(
        default="auto",
        metadata=_meta(
            "Sandbox", "Sandbox mode for ACP provider.", enum=["auto", "off"]
        ),
    )
    yolo: bool = field(
        default=False,
        metadata=_meta("YOLO Mode", "Skip tool approval confirmations."),
    )
    acp_concurrent_sessions: bool = field(
        default=False,
        metadata=_meta(
            "ACP Concurrent Sessions",
            "Run multiple ACP chat sessions on ONE backend process (multiplexing) "
            "instead of one process per session — for backends that support session "
            "interleaving. Off by default; the per-backend capability gate must also "
            "allow it.",
        ),
    )
    prompt_cache_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Prompt Caching",
            "Ask providers that support it to cache the stable prompt prefix. Reduces "
            "cost and latency on multi-turn work. No effect on providers without cache "
            "support. Turn it off to rule caching out when debugging a provider — the "
            "served prompt's ordering is unchanged either way.",
        ),
    )
    bot_name: str = field(
        default="",
        metadata=_meta(
            "Bot Name",
            "Custom name the bot identifies as in conversations. Leave empty for default.",
        ),
    )
    orchestrator_skill: bool = field(
        default=False,
        metadata=_meta(
            "Orchestrator Skill",
            "Enable agent delegation — loads the orchestrator skill with the agent roster.",
        ),
    )
    max_subagents: int = field(
        default=3,
        metadata=_meta(
            "Max SubAgents",
            "Maximum concurrent subagents. 0 = auto-size from host CPU + memory.",
        ),
    )
    spawn_min_memory_gb: float = field(
        default=4.0,
        metadata=_meta(
            "Spawn Min Memory GB",
            "Minimum available memory (GB) required to spawn a subagent. 0 disables the check.",
        ),
    )
    subagent_max_turns: int = field(
        default=100,
        metadata=_meta("SubAgent Max Turns", "Default tool-call budget per subagent."),
    )
    subagent_timeout_secs: int = field(
        default=1800,
        metadata=_meta(
            "SubAgent Timeout (seconds)",
            "Wall-clock timeout per subagent execution. 0 uses hardcoded default (1800s).",
        ),
    )
    subagent_cwd_allowed_roots: list[str] = field(
        default_factory=lambda: ["~/workspace", "~/workplace"],
        metadata=_meta(
            "SubAgent CWD Allowed Roots",
            "Directory roots under which subagent_run's cwd parameter is permitted. "
            "Values support ~ expansion. Empty list disables cwd overrides.",
        ),
    )
    log_level: str = field(
        default="WARNING",
        metadata=_meta(
            "Log Level",
            "Persistent log level for the backend logger. "
            "Applied at startup; overridden by --verbose CLI flag.",
            enum=["DEBUG", "INFO", "WARNING", "ERROR"],
        ),
    )
    soft_stop_budget_secs: float = field(
        default=10.0,
        metadata=_meta(
            "Soft-Stop Budget",
            "Seconds to wait for cooperative cancel before hard-killing the session.",
        ),
    )
    unattended_requires_verified_adapter: bool = field(
        default=False,
        metadata=_meta(
            "Unattended Requires Verified Adapter",
            "Refuse an UNATTENDED spawn — a cron fire, a loop-cycle worker, a "
            "subagent, the background/heartbeat session, an inbox or side sweep, a "
            "channel delivery, or a trigger dispatch — onto an "
            "external agent runner whose ACP adapter has no verified provenance: an "
            "`npx -y` fetch-at-launch, an adapter that changed since it was "
            "provisioned, or a runner with no catalog row. Interactive chat is never "
            "gated: a human is present to see what launched. Off by default; turn it "
            "on to require that background work only ever runs a proven adapter.",
        ),
    )
    runner_health_check_secs: int = field(
        default=3600,
        metadata=_meta(
            "Runner Health Check Interval",
            "How long a runner's measured health evidence stays current, in seconds. "
            "Past this, Settings → Agents marks the row's check overdue instead of "
            "presenting an old reading as the present state. Probing is never "
            "automatic — this only decides when a stored measurement stops counting "
            "as an answer.",
        ),
    )
    runner_idle_release_secs: int = field(
        default=1800,
        metadata=_meta(
            "Runner Idle Release",
            "How long a session may hold an agent runner without using it, in seconds. "
            "Past this the hold is released: Settings → Agents stops showing that "
            "session as the holder and the runner reads as free. The session itself is "
            "untouched — this releases the RECORD of who holds what, so a session that "
            "went quiet (or a gateway that was killed) cannot leave a runner looking "
            "permanently taken.",
        ),
    )
    durable_sessions: bool = field(
        default=False,
        metadata=_meta(
            "Durable worker sessions",
            "Let a worker's shell outlive the gateway by running it inside a tmux "
            "session on Gideon's own tmux socket. On restart the recovery sweep "
            "recomputes each session's name, finds the still-alive worker and marks "
            "the run resumable instead of aborting it — so a gateway restart mid-run "
            "stops discarding work. Requires the `tmux` binary; without it the setting "
            "has no effect and behaviour is exactly as today.",
        ),
    )

    self_qa: SelfQaConfig = field(
        default_factory=SelfQaConfig,
        metadata=_meta(
            "Self-QA companion",
            "Commit-watch QA loop: triage each new commit, drive an as-a-user scenario, file "
            "findings. Off by default.",
        ),
    )

    def __post_init__(self) -> None:
        previous = self.soft_stop_budget_secs
        bounded = max(0.5, min(60.0, float(previous)))
        if previous == bounded:
            return
        logger.warning(
            "soft_stop_budget_secs=%s out of range [0.5, 60.0]; clamped to %s",
            previous,
            bounded,
        )
        self.soft_stop_budget_secs = bounded


@dataclass
class SessionConfig:
    timeout_secs: int = field(
        default=DEFAULT_SESSION_TIMEOUT,
        metadata=_meta("Session Timeout", "Idle session timeout in seconds."),
    )
    autocompact_pct: float = field(
        default=90.0,
        metadata=_meta(
            "Auto-Compact Threshold",
            "Context usage percentage at which auto-compaction triggers (5-90).",
        ),
    )
    pool_size: int = field(
        default=0,
        metadata=_meta(
            "Warm Pool Size",
            "Number of pre-spawned ACP agent processes kept ready for instant "
            "session start. 0 disables. Only useful for ACP agents (subprocess "
            "spawn is the cost); the native runtime starts in-process with no "
            "subprocess, so the pool is unnecessary for native agents.",
        ),
    )
    pool_agent: str = field(
        default="",
        metadata=_meta(
            "Warm Pool Agent",
            "Agent name for warm pool processes. Empty string uses default_agent.",
        ),
    )
    pool_ttl_secs: int = field(
        default=1800,
        metadata=_meta(
            "Warm Pool TTL",
            "Max age in seconds for pooled processes. Stale processes are discarded at claim time. 0 disables.",  # noqa: E501
        ),
    )
    auto_archive_days: int = field(
        default=30,
        metadata=_meta(
            "Auto-Archive After",
            "Days of inactivity after which a conversation moves to Archived. "
            "Archived chats leave the active list but stay fully searchable and can "
            "be restored at any time — nothing is deleted. 0 turns auto-archive off. "
            "Pin a chat with 'never archive' to exempt it.",
        ),
    )


@dataclass
class AmbientConfig:
    """Ambient-surfaces settings (AMBIENT-SURFACES — the composable home).

    The knobs the dashboard-as-views registry, the generative-UI layer, the
    layered-surface overlay, and the menu-bar companion read. All default to the
    conservative shipped behavior so an untouched install is exactly today's
    dashboard: ``tiles_enabled`` gates the composable home, ``max_tiles`` caps a
    view's pinned tiles, ``default_refresh_ttl_secs`` is the pre-substrate tile
    refresh cadence, ``genui_enabled`` gates the generative-UI renderer,
    ``surfaces_max_layer`` is the safe-mode ceiling (0 = pure-L0), and
    ``tray_enabled`` gates the macOS tray companion.
    """

    tiles_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Composable home",
            "Enable the composable home — pin saved artifacts as self-refreshing "
            "dashboard tiles. Off leaves the dashboard as its fixed default layout.",
        ),
    )
    max_tiles: int = field(
        default=12,
        metadata=_meta(
            "Max tiles per view",
            "Cap on how many artifact tiles a single view can hold — an unbounded "
            "home is an unreadable one.",
        ),
    )
    default_refresh_ttl_secs: int = field(
        default=900,
        metadata=_meta(
            "Default tile refresh (seconds)",
            "How often a TTL-mode tile re-runs its bound data workflow (the "
            "pre-substrate refresh cadence). A view-trigger binding overrides this.",
        ),
    )
    genui_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Generative UI",
            "Enable the generative-UI layer — agent-authored widgets render through "
            "the typed component registry alongside markdown.",
        ),
    )
    surfaces_max_layer: int = field(
        default=2,
        metadata=_meta(
            "Surface layers",
            "The layered-surface ceiling (0 = pure launcher, 1 = + tiles, 2 = full). "
            "The safe-mode knob — force 0 to disable the ambient surface overlay.",
        ),
    )
    tray_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Menu-bar companion",
            "Enable the macOS menu-bar tray companion (a thin client app over the "
            "existing gateway APIs). Off by default; macOS only.",
        ),
    )


@dataclass
class CompanionConfig:
    """Companion-app settings (COMPANION-APPS — native clients over the gateway).

    The gateway can advertise itself on the local network so a companion client
    (phone/desktop) can find and pair with it without a typed URL. ``discovery_enabled``
    gates that advertisement — OFF by default, because announcing a service on the LAN is
    a posture choice the user must opt into, not a silent behavior. ``instance_name`` is
    the friendly label a client shows for this gateway; empty means the advertiser (CA-5)
    falls back to the machine hostname.
    """

    discovery_enabled: bool = field(
        default=False,
        metadata=_meta(
            "LAN discovery",
            "Advertise this gateway on the local network so companion apps can find it "
            "without a typed URL. Off by default — announcing a service on your LAN is an "
            "opt-in.",
        ),
    )
    instance_name: str = field(
        default="",
        metadata=_meta(
            "Instance name",
            "Friendly name companion apps show for this gateway. Empty falls back to the "
            "machine hostname.",
        ),
    )


PUSH_BACKENDS: tuple[str, ...] = ("webpush", "ntfy", "relay", "none")


@dataclass
class MobileConfig:
    """Phone push transport (MOBILE-COMPANION §C3 — the ``push`` target's HOW).

    WHETHER a notification reaches the phone is plan 42's rules matrix (per-(source,kind)
    targets); this section is only which transport carries it. ``webpush`` uses the
    browser's own subscription and needs a VAPID keypair (``gideon push init``);
    ``ntfy`` POSTs to a self-hosted topic URL and needs no keys; ``none`` is off.

    Neither field can leak content — every payload is ``{kind, item_id}`` by construction
    (:mod:`gideon.workspace.push`). ``ntfy_topic_url`` is nonetheless a *destination*, so it
    is treated as one: https-only, so a ping is not published in the clear.
    """

    push_backend: str = field(
        default="webpush",
        metadata=_meta(
            "Push backend",
            "How a push reaches your phone. 'webpush' uses the browser's own "
            "subscription (needs `gideon push init`); 'ntfy' publishes to a "
            "self-hosted ntfy topic; 'relay' sends ids-only pings through a "
            "stateless push-relay to the store app; 'none' sends nothing.",
            enum=list(PUSH_BACKENDS),
        ),
    )
    ntfy_topic_url: str = field(
        default="",
        metadata=_meta(
            "ntfy topic URL",
            "Full https URL of your ntfy topic (e.g. https://ntfy.example/gideon). "
            "Only used when the backend is 'ntfy'. Pings carry ids only, never content.",
        ),
    )
    relay_url: str = field(
        default="",
        metadata=_meta(
            "Push relay URL",
            "Full https URL of a push-relay instance (self-deployed, or the hosted "
            "convenience). Only used when the backend is 'relay'. The relay is "
            "stateless and forwards ids-only pings to the vendor push services — it never sees "
            "content.",
        ),
    )


@dataclass
class BrowseConfig:
    """Autonomous-browse posture (BROWSE-AUTOMATION §(a)/(d) — BA-7).

    ``user_browser_enabled`` is the connector toggle for the SECOND execution target. The
    ``gateway`` target needs no switch: it drives this machine's own per-site browser profile,
    which the operator created for that purpose. The ``user_browser`` target drives the browser
    they are personally logged into, inheriting every live session in it — so it is OFF by
    default and turning it on is the posture decision, exactly like ``companion.discovery_enabled``
    above. With it off, a ``user_browser`` task SKIPS with a typed reason and is never re-pointed
    at the gateway profile (``browse.target.connector_status``).
    """

    user_browser_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Browser control",
            "Let a browse task drive YOUR browser through the connector extension, using the "
            "sites you are already logged into. Off by default, and never available to a "
            "scheduled or unattended run.",
        ),
    )


@dataclass
class LocalModelsConfig:
    """Local model-manager knobs (LOCAL-MODEL-MANAGER-V2 §9).

    ``pressure_warn_pct`` is where the loaded-models widget's memory bar starts warning —
    a threshold, not a limit: nothing is ever blocked or unloaded automatically, because
    on a single-user machine the person watching the bar is the one who gets to decide
    what to close. ``sidecar_restart_max`` bounds how many times in a row a crashed
    sidecar child is respawned before the runner stops trying; without a bound, a provider
    whose venv is genuinely broken becomes a respawn busy-loop instead of one honest
    error. ``memory_reserve_gb`` is how much memory the fit verdict holds back for the OS
    and the inference runtime before it decides whether a model fits — raising it makes
    every verdict more conservative, and it is never a limit that blocks a download or a
    load. ``hide_unrunnable_models`` is the browse filter's default: on a small machine a
    catalog is mostly models it cannot run, so the filter starts ON and stays one click
    away. ``hf_whoami_ttl_secs`` bounds reuse of a successful or failed
    Hugging Face whoami check, avoiding a network request on every status render.
    """

    pressure_warn_pct: int = field(
        default=85,
        metadata=_meta(
            "Memory pressure warning",
            "Percent of system RAM in use at which the loaded-models bar warns. Advisory "
            "only — nothing is unloaded for you.",
        ),
    )
    sidecar_restart_max: int = field(
        default=3,
        metadata=_meta(
            "Sidecar restart limit",
            "How many times in a row a crashed model sidecar is respawned before the "
            "runner gives up and reports the failure instead.",
        ),
    )
    memory_reserve_gb: float = field(
        default=3.0,
        metadata=_meta(
            "Memory reserve",
            "Memory (GB) held back for your OS and the inference runtime, subtracted "
            "before any model-fit verdict. Raise it if models fit on paper but your "
            "machine struggles — verdicts get more cautious. It never blocks anything.",
        ),
    )
    hide_unrunnable_models: bool = field(
        default=True,
        metadata=_meta(
            "Hide models this device cannot run",
            "Keeps models that do not fit this machine's memory out of the browse list. "
            "On by default; turn it off to see the whole catalog.",
        ),
    )
    hf_whoami_ttl_secs: int = field(
        default=300,
        metadata=_meta(
            "Hugging Face token check TTL",
            "How long a successful Hugging Face whoami token check is reused, in seconds. "
            "Set to 0 to validate on every check.",
        ),
    )
    selftest_timeout_secs: int = field(
        default=60,
        metadata=_meta(
            "Local model self-test timeout",
            "Maximum time, in seconds, a single provider capability self-test may run "
            "before it reports a timeout.",
        ),
    )


@dataclass
class SourcesConfig:
    """Watched-source engine settings (WATCHED-SOURCES §Plug-in Map, SC#12).

    The knobs the :class:`~gideon.cognition.knowledge.source_engine.SourceEngine` reads each
    tick. Defaults keep an untouched install conservative: polling on, a one-hour default
    interval, a 15-minute network floor (the R1-class rate discipline — a source polls
    someone else's server, so a too-frequent poll is abusive and scraper-like), and modest
    per-poll caps. ``enabled`` is the master switch — off parks the loop so no source is
    ever fetched.
    """

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "Watched sources",
            "Enable the watched-source poll engine — feeds, pages and directories you "
            "add are polled on their schedule. Off parks the loop; nothing is fetched.",
        ),
    )
    poll_interval_default_secs: int = field(
        default=3600,
        metadata=_meta(
            "Default poll interval (seconds)",
            "How often a source is polled when it does not set its own interval. Clamped "
            "up to the network floor below.",
        ),
    )
    network_floor_secs: int = field(
        default=900,
        metadata=_meta(
            "Network poll floor (seconds)",
            "The fastest any network source is polled, regardless of its own setting. A "
            "too-frequent poll is abusive to the target server and looks like a scraper — "
            "this is the rate floor that prevents it.",
        ),
    )
    max_sources: int = field(
        default=100,
        metadata=_meta(
            "Max active sources",
            "Cap on how many enabled sources the engine arms per tick. A runaway config "
            "cannot schedule unbounded polling.",
        ),
    )
    max_items_per_poll: int = field(
        default=50,
        metadata=_meta(
            "Max items per poll",
            "How many new items one poll may ingest before the rest wait for the next "
            "cycle — a burst of back-fill cannot flood the ingestion queue in one tick.",
        ),
    )
    daily_request_budget: int = field(
        default=288,
        metadata=_meta(
            "Daily request budget per source",
            "Upper bound on network requests one source may make in a rolling day. Without "
            "it, a handful of short-interval watches is thousands of daily requests at a "
            "third party from a machine left running (enforced by the fetching providers).",
        ),
    )


@dataclass
class AppsConfig:
    """App Store settings that are not per-app (ECOSYSTEM-TOOLING T2.2).

    Two knobs, both about shipped NETWORK app sources, and they work differently on purpose:

    * ``registry_source_enabled`` is a *seed* switch. On first start with it on, the curated
      registry URL is written into ``apps/app-sources.json`` as an ordinary, removable row
      (see :func:`gideon.extensions.apps.catalog.seed_default_git_sources`). Turning it off later
      does not retract an already-seeded row (remove it in the Store, which persists);
      turning it on later seeds on the next start.
    * ``bundled_source_enabled`` is a *live filter* over the bundled default tuple, which is
      folded into every read and so has no row to remove. It is the off switch that makes
      "a Store read contacts github.com before anything has been configured" a refusable
      default rather than an unconditional one (#2528).

    Either way a source only ever contributes Store LISTINGS — the scanner-gated install
    path is unchanged, so nothing from a source runs without per-app consent.
    """

    registry_source_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Registry app source",
            "Ship the curated app registry as a default source in the Store, so community "
            "apps are discoverable out of the box. Seeded once as a removable source; off "
            "means it is never added. Listing only — installing still runs the scanner.",
        ),
    )
    bundled_source_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Bundled app source",
            "List the published first-party apps repo as a Store source. On (the default) a "
            "Store read contacts github.com to enumerate it; off means the Store reaches no "
            "network of its own and lists only local sources. Listing only — installing "
            "still runs the scanner.",
        ),
    )


@dataclass
class SkillCatalogConfig:
    """One external skill-catalog source (AGENT-PACKS §6, the ``packs.skill_catalogs`` list).

    A catalog is a named index of installable skills (a GitHub "tap" repo, a
    ``/.well-known/skills/index.json`` site). AP-6 registers each as a
    :class:`CatalogMarketplace` on the shared skills registry at COMMUNITY tier and installs
    through the same ``install_guarded`` chokepoint. AP-3 only wires the config surface; the
    ``list[dataclass]`` precedent is :class:`ProjectionRuleConfig`, so each element field
    carries ``_meta`` for the schema-reachability tests.
    """

    name: str = field(
        default="",
        metadata=_meta("Catalog name", "A short label for this skill catalog."),
    )
    url: str = field(
        default="",
        metadata=_meta(
            "Catalog URL",
            "The catalog's index endpoint or repo URL. Fetched under the CONNECTOR egress "
            "profile when the catalog is browsed (AP-6); never spawned or executed.",
        ),
    )
    kind: str = field(
        default="index",
        metadata=_meta(
            "Catalog kind",
            "How the URL is read: 'index' (a JSON skill index) or 'tap' (a git repo of "
            "skills/<slug>/SKILL.md).",
        ),
    )


@dataclass
class PacksConfig:
    """Portable-pack + skill-catalog + connector-catalog settings (AGENT-PACKS §8).

    The knobs the pack importer and the (later) catalog importer + fingerprint scanner read.
    ``skill_catalogs`` is the AP-6 list of external skill-catalog sources (each a
    :class:`SkillCatalogConfig`); ``fingerprint_enabled`` is the AP-7 project-fingerprint
    master switch (guard-flag-safe: a missing/garbage value stays ON so the propose-only
    surface is never silently disabled); ``connector_catalog_url`` is the optional published
    URL the seeded ``connector_catalog.json`` refreshes from. Defaults keep an untouched
    install conservative — no catalogs configured, fingerprinting on (it only ever
    *proposes*), no remote catalog refresh.
    """

    skill_catalogs: list[SkillCatalogConfig] = field(
        default_factory=list,
        metadata=_meta(
            "Skill catalogs",
            "External skill-catalog sources browsed + installed through the guarded skills "
            "chokepoint (AP-6). Empty by default.",
        ),
    )
    fingerprint_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Project fingerprinting",
            "Let the zero-LLM fingerprint scanner PROPOSE matching packs for a project "
            "(AP-7). It only ever proposes — never auto-installs. Off stops scanning.",
        ),
    )
    connector_catalog_url: str = field(
        default="",
        metadata=_meta(
            "Connector catalog URL",
            "Optional published URL the local connector catalog refreshes from (fetched "
            "under the CONNECTOR egress profile). Empty keeps the seeded bundled set only.",
        ),
    )


@dataclass
class LegibilityConfig:
    """Platform-legibility features (Platform-Legibility §5-§7).

    Two independent, user-facing toggles. ``discover_tips`` gates the dashboard
    "Discover" section and the Discover hub (§6) — a curated, propose-don't-write
    tour of the system that never enables anything on its own. ``context_adapters``
    gates writing routed-context adapter files (CLAUDE.md/AGENTS.md/.cursorrules)
    into an opted-in project's bound workspace (§7) — off by default because it
    writes into user project dirs.
    """

    discover_tips: bool = field(
        default=True,
        metadata=_meta(
            "Discover tips",
            "Show the Discover section on the dashboard and the Discover hub — a "
            "curated tour of the parts of Gideon you haven't tried yet, each a "
            "deep link into the feature. It only points; it never enables anything.",
        ),
    )
    context_adapters: bool = field(
        default=False,
        metadata=_meta(
            "Context Adapters",
            "When on, Gideon renders routed-context adapter files "
            "(CLAUDE.md / AGENTS.md / .cursorrules) into each opted-in project's "
            "bound workspace directory, fenced by GIDEON markers. Off by default — "
            "it writes files into your project directories.",
        ),
    )


@dataclass
class MemoryConfig:
    semantic_confidence_threshold: float = field(
        default=0.8,
        metadata=_meta(
            "Semantic Confidence Threshold",
            "Minimum similarity score for semantic search results.",
        ),
    )
    episodic_dedup_threshold: float = field(
        default=0.88,
        metadata=_meta(
            "Episodic Dedup Threshold",
            "Similarity threshold for deduplicating episodic memories.",
        ),
    )
    episodic_max_results: int = field(
        default=8,
        metadata=_meta(
            "Episodic Max Results", "Maximum episodic memory results per query."
        ),
    )
    episodic_max_count: int = field(
        default=10_000,
        metadata=_meta("Episodic Max Count", "Maximum total episodic memories stored."),
    )
    semantic_keys: list[str] = field(
        default_factory=list,
        metadata=_meta("Semantic Keys", "Keys to index for semantic search."),
    )
    l1_manifest: bool = field(
        default=True,
        metadata=_meta(
            "L1 Memory Manifest",
            "Inject only a small always-on manifest of your most-recalled facts; "
            "the agent pulls deeper memory on demand via the memory_recall tool. "
            "Off = inject full semantic + episodic memory every turn (legacy).",
        ),
    )
    active_recall: bool = field(
        default=True,
        metadata=_meta(
            "Active Recall",
            "On an interactive turn, surface query-relevant memory just before the "
            "reply (grounding it at the natural moment) — bounded by a timeout + "
            "circuit breaker. Skipped for temporary/incognito/headless turns.",
        ),
    )
    proactive_commitments: bool = field(
        default=False,
        metadata=_meta(
            "Proactive Check-ins (experimental)",
            "Let the agent infer future check-ins from conversation ('you said the "
            "migration ships Friday — I'll check Monday') and deliver ONE natural "
            "reminder per window via the heartbeat. OFF by default: a wrong check-in "
            "is intrusive, so this is opt-in. High-confidence only; capped per day; "
            "scoped to the exact agent + channel; one-tap dismiss.",
        ),
    )
    proactive_commitments_max_per_day: int = field(
        default=3,
        metadata=_meta(
            "Proactive Check-ins — Daily Cap",
            "Hard maximum active proactive check-ins per agent per day.",
        ),
    )
    active_recall_timeout_ms: int = field(
        default=1500,
        metadata=_meta(
            "Active Recall Timeout (ms)",
            "Hard budget for the pre-reply recall step; on timeout the turn "
            "proceeds without it (and the circuit breaker trips after repeats).",
        ),
    )
    auto_promote_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Auto-Promote Memory",
            "Periodically promote repeated episodic memories into durable semantic "
            "facts (the self-learning loop), unattended — guarded by a per-run cap "
            "+ a min-interval + single-flight. Off = promotion only via the button.",
        ),
    )
    auto_promote_every_n: int = field(
        default=10,
        metadata=_meta(
            "Auto-Promote Every N Consolidations",
            "Run promotion after every Nth history consolidation (lower = more "
            "frequent). Combined with the min-interval guard.",
        ),
    )
    auto_promote_max_per_run: int = field(
        default=5,
        metadata=_meta(
            "Auto-Promote Max Per Run",
            "Cap on clusters promoted in a single autonomous run (anti-runaway).",
        ),
    )
    history_idle_hours: float = field(
        default=3.0,
        metadata=_meta(
            "History Idle Hours",
            "Hours of inactivity before history consolidation.",
        ),
    )
    history_max_days: int = field(
        default=365,
        metadata=_meta("History Max Days", "Maximum days of history to retain."),
    )
    migrated: bool = field(
        default=False,
        metadata=_meta(
            "Migrated",
            "Whether a legacy memory migration imported records into the vector store. "
            "Managed by the migration operation.",
        ),
    )
    vault_mode: str = field(
        default="off",
        metadata=_meta(
            "Memory Vault (Obsidian)",
            "off = no vault. mirror = write memory out as a browsable markdown vault "
            "(Obsidian-compatible: YAML frontmatter + [[wikilinks]] + graph view), "
            "regenerated from the store and never read back. two_way = also read your "
            "edits back into memory on the next sync — a page you change wins over the "
            "stored value, and a page the sync cannot parse is left untouched and "
            "reported in Memory → Health rather than overwritten.",
        ),
    )
    vault_path: str = field(
        default="memory-vault",
        metadata=_meta(
            "Vault Path",
            "Where the markdown vault is written. Relative paths resolve under "
            "the Gideon config dir (~/.gideon); absolute paths are used as-is.",
        ),
    )
    graph_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Entity Graph",
            "Link memories to the people, projects and tools they mention, so "
            "'what do I know about X?' can be answered by following links instead "
            "of hoping similarity search finds everything. Matching is exact-name "
            "and costs no tokens or LLM calls. Off = every graph surface falls back "
            "to today's search behavior.",
        ),
    )
    push_context: bool = field(
        default=False,
        metadata=_meta(
            "Volunteer Related Memory",
            "When a message mentions someone or something the entity graph knows, "
            "offer up to 3 linked memories for that turn — even when they share no "
            "words with what you typed. Costs no tokens or LLM calls beyond the small "
            "block it adds. Off by default because it puts context in front of the "
            "model you didn't ask for; the Health tab reports how often what it "
            "volunteered actually got used.",
        ),
    )
    push_min_confidence: float = field(
        default=0.7,
        metadata=_meta(
            "Volunteer Confidence",
            "How sure the match must be before memory is volunteered. Higher = only "
            "explicit aliases and exact names; lower also admits looser matches "
            "(more offered, more of it irrelevant).",
        ),
    )
    graph_topology_in_context: bool = field(
        default=False,
        metadata=_meta(
            "Topology Orientation Block",
            "At the start of a new session, add a tiny map of the neighbourhoods in "
            "your memory graph ('people around project X', 'the tools cluster') so the "
            "assistant knows which areas exist before it searches. Off by default: it "
            "spends a little context on every new session, and it says nothing useful "
            "until the graph has enough links to form distinct groups.",
        ),
    )
    slot_size_cap: int = field(
        default=1400,
        metadata=_meta(
            "Slots Budget (characters)",
            "How much of every session's context the always-injected Slots block may "
            "cost. Slots are the registers read on every session regardless of what you "
            "asked (persona, preferences, pending items…), so this is a spend you pay "
            "constantly: raising it buys the assistant more standing context, lowering it "
            "truncates the block. Clamped to 200-4000 — the per-slot caps that decide "
            "which individual register is full are fixed in code, not here.",
        ),
    )
    holder_attribution: bool = field(
        default=False,
        metadata=_meta(
            "Attribute Claims to Who Said Them",
            "Record WHOSE claim a memory is (you, the assistant, a named person, or an "
            "outside source) instead of storing everything as plain fact, and render it "
            "that way in context ('Alex believes…'). Second-hand claims are capped "
            "lower, and a lower-authority claim can never retire something you said. "
            "Off by default — with it off, every memory is stored unattributed exactly "
            "as before.",
        ),
    )


@dataclass
class DashboardConfig:
    url: str = field(
        default="",
        metadata=_meta(
            "Dashboard URL",
            "Public URL for the dashboard (used in links delivered to external channels).",
        ),
    )
    public_url: str = field(
        default="",
        metadata=_meta(
            "Public URL",
            "Set this ONLY if you reach this dashboard from the internet through a "
            "TLS-terminating tunnel or reverse proxy (e.g. https://pc.example.com). It "
            "hardens the session cookie (Secure), allows wss:// to that host, and is a "
            "precondition for trusting proxy headers. Distinct from Dashboard URL, which "
            "is only used for links.",
        ),
    )
    trusted_proxies: list[str] = field(
        default_factory=list,
        metadata=_meta(
            "Trusted Proxies",
            "Addresses or CIDR blocks of the proxy/tunnel in front of this gateway. "
            "X-Forwarded-Proto / X-Forwarded-For are honored ONLY from these peers — "
            "anyone else can forge them. Empty (the default) trusts none.",
        ),
    )
    restore_sessions: bool = field(
        default=False,
        metadata=_meta(
            "Restore Sessions",
            "Re-open recently active sessions on startup.",
        ),
    )
    restore_window_minutes: int = field(
        default=30,
        metadata=_meta(
            "Restore Window Minutes",
            "Time window (minutes) for session restoration (0-1440). 0 = restore all.",
        ),
    )
    user_name: str = field(
        default="",
        metadata=_meta(
            "Operator Name",
            "How the system addresses the operator. Set during first-run onboarding; "
            "instance-level (single-user, self-hosted) so it follows the user across "
            "browsers/machines. Empty = onboarding not yet completed.",
        ),
    )
    username: str = field(
        default="",
        metadata=_meta(
            "Username",
            "Short attribution handle stamped onto records you create (tasks, "
            "comments, memories) — lowercase letters, digits, '-' and '_'. It is a "
            "label, NOT a credential: nothing authenticates or authorizes against "
            "it. Suggested from your operator name at first run. Renaming affects "
            "future writes only; existing records keep the name they were written "
            "with. Empty = writes carry no attribution (the default behavior).",
        ),
    )
    merge_queued_messages: bool = field(
        default=False,
        metadata=_meta(
            "Merge Queued Messages",
            "Concatenate follow-up messages while the agent is busy instead of queueing them separately.",  # noqa: E501
        ),
    )
    auto_tag_sessions: bool = field(
        default=True,
        metadata=_meta(
            "Auto-Tag Sessions",
            "When a chat's title is auto-generated, also propose and assign tags in the "
            "same pass — existing tags where they fit, at most 1-2 new ones otherwise. "
            "Never touches chats you've already tagged, or incognito/temporary chats.",
        ),
    )
    mcp_probe_timeout_secs: int = field(
        default=15,
        metadata=_meta(
            "MCP Probe Timeout",
            "Seconds to wait for MCP server handshake during probe (5-120).",
        ),
    )
    widget_density: str = field(
        default="more",
        metadata=_meta(
            "Widget Density",
            "How aggressively the agent uses inline widgets. "
            "'more' encourages widgets for any visual content; "
            "'less' limits to only when markdown is clearly insufficient.",
            enum=["more", "less"],
        ),
    )
    send_on_enter: bool = field(
        default=True,
        metadata=_meta(
            "Send on Enter",
            "Enter sends the message (Shift+Enter for newline). When off, Enter "
            "inserts a newline and Cmd/Ctrl+Enter sends.",
        ),
    )
    show_timestamps: bool = field(
        default=False,
        metadata=_meta("Show Timestamps", "Display a timestamp on each chat message."),
    )
    show_thinking_inline: bool = field(
        default=False,
        metadata=_meta(
            "Show Thinking Inline",
            "Show intermediate reasoning between tool calls instead of collapsing it.",
        ),
    )
    simplified_tool_names: bool = field(
        default=False,
        metadata=_meta(
            "Simplified Tool Names",
            "Inline tool pills show a simplified purpose instead of the exact command.",
        ),
    )
    followup_chips: bool = field(
        default=True,
        metadata=_meta(
            "Follow-up suggestions",
            "After each reply, show 2-3 suggested next messages (one small background model "
            "call; never blocks the turn). Skipped for temporary/incognito chats; silent when "
            "no model is bound.",
        ),
    )
    offer_check_work: bool = field(
        default=True,
        metadata=_meta(
            "Offer 'Check this work'",
            "After a turn that claims a multi-step task is complete (3+ tool calls plus "
            "completion language), offer a 'Check this work' chip beside the follow-up "
            "suggestions. The chip only OFFERS — check-work runs when you click it, "
            "never automatically, so the cost and latency stay yours to spend.",
        ),
    )
    stream_reveal: str = field(
        default="smooth",
        metadata=_meta(
            "Streaming text reveal",
            "smooth: steady word-by-word reveal decoupled from network chunks (never lags). "
            "immediate: render each chunk the instant it arrives.",
            enum=["smooth", "immediate"],
        ),
    )
    auto_open_browser: bool = field(
        default=True,
        metadata=_meta(
            "Auto Open Browser",
            "Open the dashboard URL in the default browser on gateway startup.",
        ),
    )
    update_dev_mode: bool = field(
        default=False,
        metadata=_meta(
            "Developer Update Mode",
            "Git checkouts only: update on every new commit on the current branch "
            "instead of only when a new release TAG exists. Off (default) means the "
            "in-app updater rides releases like every other install kind; on is the "
            "contributor 'track main' behavior. No effect on pip/container/desktop "
            "installs (they always update per release).",
        ),
    )
    screen_share_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Screen Sharing in Chat",
            "Master opt-in for the composer's 'Share screen' control (MULTIMODAL-IO "
            "§5). With it on, a chat turn can carry ONE frame of a screen or window "
            "you explicitly picked in the browser's own share dialog: the frame is "
            "held in memory for that single turn, never written to disk, and dropped "
            "the moment it is used. OFF by default — with it off the control is "
            "hidden AND the server refuses any frame, so nothing can share your "
            "screen by asking nicely.",
        ),
    )
    document_editing: bool = field(
        default=False,
        metadata=_meta(
            "Edit Documents in Place",
            "Open a generated Word document in an editor instead of download-only. A save "
            "RE-RENDERS the file, so constructs the model cannot hold are lost — the editor "
            "names them before the first edit and again at save, and the previous version is "
            "one revert away. OFF by default, and enforced server-side too (DFE-5 §C6).",
        ),
    )
    terminal: dict = field(
        default_factory=lambda: {"enabled": True},
        metadata=_meta(
            "Terminal",
            "Terminal panel configuration. Enabled by default (powers the CLI "
            "panel + per-provider Sign-in terminal); set enabled=false to hide.",
        ),
    )


@dataclass
class AgentProfile:
    provider: str = field(
        default="",
        metadata=_meta(
            "Provider",
            "Agent runtime backend: 'native' (in-process loop, governed by "
            "Settings → Models) or 'acp:<cli>' (external CLI). Empty inherits the "
            "global agent.provider default.",
        ),
    )
    provider_agent: str = field(
        default="",
        metadata=_meta(
            "Provider Agent", "ACP provider agent name (modeId for session/set_mode)."
        ),
    )
    acp_mode: str = field(
        default="",
        metadata=_meta(
            "ACP Mode",
            "ACP permission/operating mode for adapters that expose one "
            "(claude-code/codex: default, acceptEdits, plan, dontAsk, "
            "bypassPermissions; set via session/set_config_option). Distinct from "
            "Approval Mode (the host gate). Empty inherits the adapter default; "
            "ignored by runtimes with no separate mode axis (the default dialect).",
        ),
    )

    default_dir: str = field(
        default="",
        metadata=_meta(
            "Default Directory",
            "Optional working directory this agent opens in. Empty inherits the "
            "workspace root. Overridable per-session.",
        ),
    )
    memory_store: str = field(
        default="",
        metadata=_meta(
            "Memory Store",
            "Optional memory provider for this agent. Empty uses the filesystem "
            "fallback scoped by working directory.",
        ),
    )
    description: str = field(
        default="",
        metadata=_meta("Description", "Human-readable agent description."),
    )
    system_prompt: str = field(
        default="",
        metadata=_meta(
            "System Prompt", "System prompt injected at session start for this agent."
        ),
    )
    voice: str = field(
        default="",
        metadata=_meta(
            "Voice",
            "WHO the agent is — tone, opinions, bluntness, persona — kept separate "
            "from the operating rules (System Prompt) and injected high-priority so "
            "personality survives long prompts.",
        ),
    )
    natural_voice: bool = field(
        default=False,
        metadata=_meta(
            "Natural Voice",
            "Ask this agent for plainer, less machine-sounding prose — a named set "
            "of patterns to avoid, not a persona. Travels with the agent into every "
            "conversation that binds it; a conversation can override it for itself "
            "from the composer. Distinct from Voice (WHO the agent is) and from "
            "voice profiles (speech). Never changes facts, caveats or refusals.",
        ),
    )
    model: str = field(
        default="",
        metadata=_meta("Model", "Default model for this agent. Overridable per-chat."),
    )
    approval_mode: str = field(
        default="",
        metadata=_meta(
            "Approval Mode",
            "Tool approval mode: auto, interactive, or empty (inherit global).",
        ),
    )
    skills: list = field(
        default_factory=list,
        metadata=_meta("Skills", "List of skill names loaded for this agent."),
    )
    tools: list = field(
        default_factory=list,
        metadata=_meta("Tools", "List of allowed tool name patterns for this agent."),
    )
    triggers: list = field(
        default_factory=list,
        metadata=_meta(
            "Triggers",
            "Referenced lifecycle-trigger IDs. A lifecycle trigger fires ONLY for "
            "agents that list it here — there is no global firing. Empty = no "
            "triggers for this agent.",
        ),
    )
    source: str = field(
        default="gideon",
        metadata=_meta("Source", "Agent origin: gideon, marketplace, or builtin."),
    )
    specialty: str = field(
        default="",
        metadata=_meta(
            "Specialty",
            "One line: what this agent is the specialist for. Drives the routing "
            "suggestion's embedding match. Empty = never suggested.",
        ),
    )
    route_hints: str = field(
        default="",
        metadata=_meta(
            "Routing Hints",
            "Comma-separated example utterances / trigger phrases that should route "
            "to this agent (the same authoring vocabulary as workflow match text).",
        ),
    )


@dataclass
class MemoryStoreConfig:
    description: str = field(
        default="",
        metadata=_meta("Description", "Human-readable purpose of this memory store."),
    )


@dataclass
class SkillsConfig:
    max_triggered: int = field(
        default=3,
        metadata=_meta(
            "Max Triggered", "Maximum number of skills to load per message (≥1)."
        ),
    )
    auto_create_from_sessions: bool = field(
        default=False,
        metadata=_meta(
            "Auto-Create Skills",
            "When true, analyze each session after completion and synthesize a reusable "
            "SKILL.md when a non-trivial multi-step procedure is detected. Generated "
            "skills live under skills/auto/ so they never collide with hand-authored "
            "skills. Disabled by default.",
        ),
    )
    auto_refine_on_deviation: bool = field(
        default=False,
        metadata=_meta(
            "Auto-Refine Skills",
            "When true, update an existing auto-created skill if the agent succeeds "
            "via a different tool sequence than documented. Requires "
            "auto_create_from_sessions. Disabled by default.",
        ),
    )
    auto_min_tool_calls: int = field(
        default=5,
        metadata=_meta(
            "Auto Min Tool Calls",
            "Minimum tool calls in a session for it to qualify for skill extraction "
            "(≥2). Lower values produce more skills but reduce quality.",
        ),
    )
    auto_similarity_threshold: float = field(
        default=0.85,
        metadata=_meta(
            "Auto Similarity Threshold",
            "Skip creation when an existing skill's description has keyword overlap "
            "≥ this fraction with the synthesized description (0.0-1.0). Prevents "
            "near-duplicate skills.",
        ),
    )
    progressive_disclosure_threshold: int = field(
        default=8,
        metadata=_meta(
            "Progressive Disclosure Threshold",
            "When more than this many skills match a turn, inject only their compact "
            "INDEX (name + description) and let the agent pull full bodies on demand "
            "via skill_invoke — instead of inlining every matched body. Token "
            "efficiency at scale; 0 disables (always inline). Default 8.",
        ),
    )

    def __post_init__(self) -> None:
        for key, floor in (("max_triggered", 1), ("auto_min_tool_calls", 2)):
            value = getattr(self, key)
            if value < floor:
                logger.warning("%s %d < %d, using %d", key, value, floor, floor)
                object.__setattr__(self, key, floor)
        if not 0.0 <= self.auto_similarity_threshold <= 1.0:
            logger.warning(
                "auto_similarity_threshold %.2f out of range [0.0, 1.0], using 0.85",
                self.auto_similarity_threshold,
            )
            object.__setattr__(self, "auto_similarity_threshold", 0.85)
        if self.auto_refine_on_deviation and not self.auto_create_from_sessions:
            logger.warning(
                "auto_refine_on_deviation requires auto_create_from_sessions; disabling auto_refine_on_deviation"
            )
            object.__setattr__(self, "auto_refine_on_deviation", False)
        object.__setattr__(
            self,
            "progressive_disclosure_threshold",
            max(0, self.progressive_disclosure_threshold),
        )


@dataclass
class KnowledgeConfig:
    """Knowledge-store semantics (WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS §2.1).

    The knobs here all govern how much a synthesis loop is allowed to write and how long
    what it wrote stays trusted. They are config rather than constants because the right
    answer depends on how the owner uses the store: a research-heavy user wants larger
    reports, and someone tracking fast-moving facts wants shorter default expiry.
    """

    ocr_max_bytes: int = field(
        default=10 * 1024 * 1024,
        metadata=_meta(
            "OCR Byte Limit",
            "Maximum image bytes passed to an OCR backend for one ingestion. Images over "
            "the remaining budget are skipped and reported rather than silently truncated.",
        ),
    )
    idempotent_persist: bool = field(
        default=True,
        metadata=_meta(
            "Idempotent Knowledge Writes",
            "Resolve a knowledge write by its logical identity (kind + title) and skip it "
            "entirely when the content is unchanged. This is what stops a retried, resumed "
            "or rewound synthesis node from writing a second near-identical article that "
            "later reads as independent corroboration. Off = every persist inserts.",
        ),
    )
    require_citations: bool = field(
        default=True,
        metadata=_meta(
            "Require Citations On Synthesis",
            "Refuse to store a synthesized item (insight, report, overview) with no "
            "citations unless it is explicitly marked unsourced. An unsourced synthesis is "
            "indistinguishable from a confident guess once it is being retrieved as fact.",
        ),
    )
    report_budget_chars: int = field(
        default=40_000,
        metadata=_meta(
            "Report Size Budget",
            "Largest a single `report` knowledge item may be, in characters. Exceeding it "
            "returns a condense-and-retry error rather than failing the run, so the "
            "synthesizing stage can shorten and try again.",
        ),
    )
    default_ttl: str = field(
        default="",
        metadata=_meta(
            "Default Knowledge Expiry",
            "Optional default expiry for newly persisted items (e.g. `30d`, `12h`). Blank "
            "means knowledge does not expire unless a write asks for it. Expiry demotes an "
            "item in retrieval rather than deleting it — a stale fact is still evidence of "
            "what was believed.",
        ),
    )
    max_mentions_per_claim: int = field(
        default=20,
        metadata=_meta(
            "Max Sources Per Claim",
            "How many independent sources a single claim will accumulate before it stops "
            "recording new ones. Confidence saturates long before this; the cap exists so a "
            "high-traffic claim cannot grow its evidence list without bound.",
        ),
    )
    synthesis_window: int = field(
        default=20,
        metadata=_meta(
            "Synthesis Window",
            "How many recent findings a long-running watcher's synthesis stage sees per cycle. "
            "Without a window, cycle 50 carries all 50 cycles of findings and every cycle costs "
            "more than the last — a run that gets slower and more expensive until it hits a "
            "context limit, with nothing indicating why.",
        ),
    )
    lint_every_n_persists: int = field(
        default=12,
        metadata=_meta(
            "Knowledge Lint Cadence",
            "Writes between semantic lint passes. Counted in WRITES rather than hours: a store "
            "nobody added to does not need linting, and a busy week needs it more than once.",
        ),
    )
    embed_batch_size: int = field(
        default=32,
        metadata=_meta(
            "Embedding Batch Size",
            "Chunk texts sent to the embedding provider per call. Higher is faster to import "
            "and cheaper in round trips; if a provider rejects a batch this large, the batch "
            "is split in half automatically rather than failing the import, so a wrong value "
            "costs a few extra calls rather than a broken library.",
        ),
    )
    embed_retry_budget: int = field(
        default=3,
        metadata=_meta(
            "Embedding Retry Budget",
            "Attempts per embedding call before a batch is split (or, for a single chunk, "
            "stored without a vector and logged). Exponential backoff between attempts, so 3 "
            "rides out a rate-limit blip without making a failed import look like a hang.",
        ),
    )
    maintenance_max_staleness_secs: int = field(
        default=900,
        metadata=_meta(
            "Graph Maintenance Staleness Window",
            "How long index work may wait while an import is still running. Graph maintenance "
            "normally waits for the ingest queue to drain, so a bulk import costs ONE pass "
            "instead of one per item — but a pipeline that never drains would starve it "
            "forever, so dirt older than this runs anyway. Lower = fresher graph during long "
            "imports; higher = fewer passes.",
        ),
    )
    similarity_min_score: float = field(
        default=0.55,
        metadata=_meta(
            "Similarity Edge Floor",
            "Minimum cosine similarity for two items to be linked by a similarity edge. Sits "
            "deliberately ABOVE the vector arm's retrieval floor (0.25): a search hit only has "
            "to be worth RANKING against the other hits for one query the user just typed, "
            "while an edge is a standing claim that two items are related — read later, by "
            "someone who never saw the query that justified it. So the edge is the stronger "
            "claim and pays the higher bar. 0.55 rather than a little above 0.25 because "
            "retrieval floors a short QUERY against a passage, where scores compress downward, "
            "while both sides of an edge are full passages: a floor near the retrieval one "
            "connects nearly every pair and the graph becomes a hairball. Lower fills the graph "
            "with near-orthogonal neighbours nobody asked about; higher leaves genuinely "
            "related items unlinked.",
        ),
    )
    similarity_top_k: int = field(
        default=8,
        metadata=_meta(
            "Similarity Edges Per Item",
            "Strongest neighbours kept per item when the pass runs. This is the OUTBOUND "
            "budget for one item, so it bounds the pass's write volume but not an item's total "
            "degree — a popular item still collects inbound edges from every neighbour that "
            "picked it, which is what the degree ceiling is for.",
        ),
    )
    similarity_degree_cap: int = field(
        default=32,
        metadata=_meta(
            "Similarity Degree Ceiling",
            "Hard per-item edge ceiling counting INBOUND edges too. Top-K alone cannot bound "
            "degree: a hub item that everything else finds similar accumulates one inbound "
            "edge per neighbour and no outbound budget of its own ever stops it, so one item "
            "ends up adjacent to the whole store and every traversal through it is a fan-out. "
            "The weakest edges are dropped first when an item reaches the cap. Set it at or "
            "above the top-K, or the cap immediately discards edges the pass just chose.",
        ),
    )
    rerank_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Relevance Reranker",
            "Reorder hybrid knowledge-search candidates with the active reasoning model. Off "
            "by default because it adds latency; an unusable response keeps the RRF order.",
        ),
    )
    rerank_max_candidates: int = field(
        default=32,
        metadata=_meta(
            "Relevance Reranker Candidate Limit",
            "Maximum fused candidates sent to the reasoning model for one search.",
        ),
    )
    consolidate_min_cluster: int = field(
        default=5,
        metadata=_meta(
            "Smallest Consolidation Cluster",
            "Fewest related items worth spending one model call to merge. Below about five, a "
            "summary loses more detail than it saves space.",
        ),
    )
    session_brief_max_tokens: int = field(
        default=800,
        metadata=_meta(
            "Session Brief Budget",
            "Token ceiling for the project digest injected at the start of every workflow run in "
            "a project. Small by default because it is paid on EVERY run — a generous budget "
            "becomes a permanent cost nobody attributes to the right feature. Items are dropped "
            "whole when the budget binds, and the brief says how many it left out.",
        ),
    )
    conflict_model_pass: bool = field(
        default=True,
        metadata=_meta(
            "Semantic Conflict Check",
            "After the free deterministic check, send claims it could not separate to one "
            "fast-model call to look for contradictions. Off leaves only the provable conflicts "
            "flagged — cheaper, and it still catches the numeric and polarity cases.",
        ),
    )
    consolidate_min_hours: int = field(
        default=6,
        metadata=_meta(
            "Hours Between Consolidation Passes",
            "Floor between consolidation sweeps. The pass is expensive and its input barely "
            "changes minute to minute, so a tighter cadence pays repeatedly for the same answer.",
        ),
    )
    auto_ingest_artifacts: bool = field(
        default=True,
        metadata=_meta(
            "Index Artifacts For Search",
            "Mirror text artifacts (markdown, HTML, text, JSON, CSV) into knowledge search so "
            "something you wrote into an artifact is findable from the one place you look for "
            "what you know. Artifacts stay in the Artifacts library — they are never listed as "
            "knowledge items, only found by a search. Indexing is local: a mirrored artifact "
            "never reaches a model. Off stops new artifacts being indexed and removes nothing "
            "already indexed.",
        ),
    )
    vault_mode: str = field(
        default="off",
        metadata=_meta(
            "Knowledge Vault (Obsidian)",
            "off = knowledge lives only in the database. mirror = also write every item out "
            "as a plain markdown file you can read, grep and back up without Gideon "
            "(YAML frontmatter carrying identity and relations + [[wikilinks]]), regenerated "
            "from the store and never read back. two_way = also read your edits back: a file "
            "you change in a text editor wins, a file you delete is not re-created, and a "
            "file changed on BOTH sides is left exactly as you wrote it and reported instead "
            "of being overwritten. Uses the same projector, page format and conflict rules as "
            "the memory vault.",
        ),
    )
    vault_path: str = field(
        default="knowledge-vault",
        metadata=_meta(
            "Knowledge Vault Path",
            "Where the markdown projection is written. Relative paths resolve under the "
            "Gideon config dir (~/.gideon); absolute paths are used as-is.",
        ),
    )


@dataclass
class RoutingWeightsConfig:
    """Score weights for the learned routing stage (MODEL-ROUTING-TELEMETRY §4.2)."""

    success: float = field(
        default=0.60,
        metadata=_meta(
            "Success Weight",
            "How much a model's observed success rate counts toward its routing score.",
        ),
    )
    feedback: float = field(
        default=0.40,
        metadata=_meta(
            "Feedback Weight",
            "How much observed output quality (ledger/judge feedback) counts toward a "
            "model's routing score. With no feedback recorded yet, this weight collapses "
            "onto the success rate rather than penalizing an unrated model.",
        ),
    )


@dataclass
class RoutingConfig:
    """Telemetry-driven model routing (MODEL-ROUTING-TELEMETRY §7).

    Routing REORDERS the models a user already bound to a use case; it never invents
    a model or changes what resolution means. ``enabled`` is the master switch and is
    OFF by default: with it off, resolution walks the bound order exactly as it always
    did. Per-use-case mode and pin deliberately live NOT here but in
    ``use_case_settings/{uc}.json`` + ``routing_policy.json`` — they are
    bindings-adjacent state, beside the use case's other behavior settings.
    """

    enabled: bool = field(
        default=False,
        metadata=_meta(
            "Enable Routing",
            "Master switch. When off, every use case resolves in the exact order you "
            "bound its models. Turn it on to let Gideon prefer a local model for "
            "work it handles well and fall back to a cloud model when it can't.",
        ),
    )
    local_timeout_secs: float = field(
        default=20.0,
        metadata=_meta(
            "Local Attempt Timeout (seconds)",
            "How long a local model gets before the call falls back to the next model "
            "you bound. Keeps a slow local model from stalling background work.",
        ),
    )
    min_samples: int = field(
        default=5,
        metadata=_meta(
            "Minimum Samples",
            "How many recorded calls a model needs for a kind of request before its "
            "measured score is allowed to influence order. Below this, the simple "
            "local-first rule stands.",
        ),
    )
    weights: RoutingWeightsConfig = field(
        default_factory=RoutingWeightsConfig,
        metadata=_meta(
            "Score Weights", "How success and quality combine into one score."
        ),
    )
    hysteresis: float = field(
        default=0.05,
        metadata=_meta(
            "Hysteresis Margin",
            "How much better a model's score must be before the order actually changes. "
            "Prevents routing flip-flopping between two near-equal models.",
        ),
    )
    cloud_quality_margin: float = field(
        default=0.10,
        metadata=_meta(
            "Cloud Quality Margin",
            "How much better a cloud model must score than a local one to be tried "
            "first. Free and private wins ties.",
        ),
    )
    energy_sampling: bool = field(
        default=False,
        metadata=_meta(
            "Energy Sampling",
            "Record a rough energy estimate for local calls, so local cost is visible "
            "as something other than $0.",
        ),
    )
    reproposal_cooldown_days: int = field(
        default=14,
        metadata=_meta(
            "Re-proposal Cooldown (days)",
            "After you reject a routing suggestion, how long before the same change "
            "may be suggested again.",
        ),
    )


@dataclass
class RemediationConfig:
    """Health-scored self-remediation engine tuning (PLATFORM-RESILIENCE §4).

    The engine runs as one heartbeat-driven maintenance job. ``enabled`` is guard-class
    only in the sense that disabling it restores today's heartbeat maintenance (kept
    callable), so it defaults ON but is a plain toggle. The caps are the stopping
    conditions: reach ``target_score`` or spend ``max_cost_usd`` (per run), whichever
    first. Cadence adapts: healthy → ``idle_minutes_healthy`` between runs, degraded →
    ``tick_minutes_degraded``.
    """

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "Remediation Engine",
            "Run the health-scored maintenance engine (FTS/embedding re-index, orphan "
            "prune, skill aging) as one background job. Disabling it falls back to the "
            "legacy per-tick heartbeat maintenance.",
        ),
    )
    target_score: int = field(
        default=90,
        metadata=_meta(
            "Target Health Score",
            "The engine stops a run once the health score reaches this (0-100).",
        ),
    )
    max_cost_usd: float = field(
        default=1.0,
        metadata=_meta(
            "Max Cost / Run",
            "Dollar ceiling for judgment-lane (model-touching) remediation work in one "
            "run. Deterministic jobs (re-index, prune) are free and never blocked.",
        ),
    )
    idle_minutes_healthy: int = field(
        default=60,
        metadata=_meta(
            "Idle Cadence (healthy)", "Minutes between runs when healthy (score ≥95)."
        ),
    )
    tick_minutes_degraded: int = field(
        default=5,
        metadata=_meta(
            "Tick Cadence (degraded)", "Minutes between runs when degraded."
        ),
    )


@dataclass
class ResilienceConfig:
    """Platform-resilience knobs (PLATFORM-RESILIENCE §7).

    Two guard-class switches: the Doctor health surface and the no-model
    degraded-mode indicator. Both are **guard-class** — a missing or unknown value
    parses as ENABLED (fail-safe, §5 tenet): a config typo must not silently hide the
    Doctor or the degraded chip, which are the surfaces that make a degraded system
    legible. Plus the platform default mid-turn message policy (§6). The
    remediation-engine sub-config (target-score / max-cost / idle cadence) is a later
    session's field.
    """

    doctor_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Doctor",
            "Show the Doctor health surface (Settings → Doctor + GET /api/doctor). "
            "Guard-class: a missing/unknown value keeps it ON.",
            guard_class=True,
            safe_values=[True],
        ),
    )
    degraded_indicator: bool = field(
        default=True,
        metadata=_meta(
            "Degraded-Mode Indicator",
            "Show the no-model degraded-mode chip in the shell (and GET "
            "/api/resilience/degraded) when a model-dependent surface is running on its "
            "LLM-free floor. Guard-class: a missing/unknown value keeps it ON.",
            guard_class=True,
            safe_values=[True],
        ),
    )
    mid_turn_policy: str = field(
        default="queue",
        metadata=_meta(
            "Mid-Turn Message Policy",
            "What happens to a follow-up message sent while a turn is still "
            "generating: 'queue' (deliver it next turn — the default, safe behavior), "
            "'steer' (inject it into the answer being written, when the running agent "
            "supports that — otherwise it queues), or 'cancel_and_replace' (cancel the "
            "in-flight answer and start fresh with the new message). Applies to "
            "interactive turns only; unattended work (loops, cron, subagents) always "
            "queues. A per-channel override wins over this platform default.",
            enum=["queue", "steer", "cancel_and_replace"],
        ),
    )
    cancel_replace_min_interval_secs: float = field(
        default=2.0,
        metadata=_meta(
            "Cancel-and-Replace Debounce",
            "Minimum seconds between cancel-and-replace actions on one session, so a "
            "burst of rapid follow-ups produces ONE cancel + the last message (the "
            "intermediate ones coalesce) rather than N cancels.",
        ),
    )
    remediation: RemediationConfig = field(
        default_factory=RemediationConfig,
        metadata=_meta(
            "Remediation Engine", "Health-scored maintenance engine tuning."
        ),
    )


@dataclass
class WorkflowsConfig:
    """Workflow engine config (WORKFLOWS-V2).

    The old shape held surfacing knobs (`match_threshold` for the embedding matcher);
    that feature was deleted, and the namespace is reused rather than renamed — the
    plan's clean-break/namespace-reuse call. `match_threshold` RETURNS here with a new
    owner (WF2UNI-11): the UNIVERSAL-PLANNING tiered matcher's T4 embedding tie-breaker
    now reads it as the cosine floor below which an embedding is too weak to unseat a
    keyword tie. It is a real reader this time — not the inert knob it was under the old
    SOP feature — so the field is live and wired through all four config points.
    `enabled` keeps its meaning as the feature kill-switch."""

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "Enabled",
            "Master switch for the workflow engine. Turning it off stops new runs "
            "from starting without touching stored definitions.",
        ),
    )
    self_schedule_max_outstanding: int = field(
        default=20,
        metadata=_meta(
            "Self-Scheduled Tasks — Max Outstanding",
            "How many enabled automations the agent may hold at once via set_onetime_task / "
            "set_recurring_task. The bound exists because a self-scheduling agent can create "
            "work faster than it retires it: each task it parks wakes it again later, and an "
            "unbounded fan-out of clocks is how a helpful loop becomes a runaway one. Counted "
            "over ENABLED agent-created automations, so pausing one frees a slot without "
            "deleting it.",
        ),
    )
    default_node_timeout_total_secs: int = field(
        default=900,
        metadata=_meta(
            "Node Timeout — Total",
            "Wall-clock cap for one node, in seconds. 0 disables it.",
        ),
    )
    default_node_timeout_stall_secs: int = field(
        default=300,
        metadata=_meta(
            "Node Timeout — Stall",
            "Kill a node after this many seconds with NO progress, even when it is "
            "under the total cap. Progress events reset the clock, so a slow-but-"
            "working node survives while a wedged one does not.",
        ),
    )
    model_tier_reasoning: str = field(
        default="reasoning",
        metadata=_meta(
            "Model Tier — Reasoning",
            "Which model use case a node asking for the `reasoning` tier resolves to. "
            "Templates name an intent, never a model, so they stay portable.",
        ),
    )
    model_tier_standard: str = field(
        default="orchestration",
        metadata=_meta(
            "Model Tier — Standard",
            "Use case for the `standard` tier. Distinct from `fast` on purpose: if both "
            "collapsed to one use case the three tiers would be decorative, and a node "
            "asking for a mid-capability model would silently get the cheapest one.",
        ),
    )
    model_tier_fast: str = field(
        default="background",
        metadata=_meta("Model Tier — Fast", "Use case for the `fast` tier."),
    )
    match_threshold: float = field(
        default=0.62,
        metadata=_meta(
            "Template Match Threshold",
            "How confident the embedding tie-breaker must be to override a keyword tie when two "
            "templates score alike (0-1). Higher composes more readily; lower lets a semantic "
            "near-match win. Only consulted on a tie — keyword matches always decide first.",
        ),
    )
    surface_mode_default: str = field(
        default="off",
        metadata=_meta(
            "New Workflow Surfacing",
            "What a NEWLY authored workflow does before you opt it in: `off` never surfaces "
            "itself (explicit /workflow always works), `passive` injects its guidance, `suggest` "
            "may propose running itself. Defaults to off — auto-trigger-by-default is the mistake "
            "that made pasted content fire workflows.",
            choices=["off", "passive", "suggest"],
        ),
    )
    max_materialized_per_foreach: int = field(
        default=20,
        metadata=_meta(
            "Task Fan-Out Cap",
            "The most Tasks one foreach node may put on your board. A 200-item fan-out would "
            "otherwise bury every other task; the run still executes all items — only the board "
            "rows are capped, and the run reports what it withheld.",
        ),
    )
    confirmation_ttl_secs: int = field(
        default=7 * 24 * 3600,
        metadata=_meta(
            "Approval Lifetime",
            "How long a pending approval stays live. A week, because the realistic case is being "
            "away — a gate expiring overnight turns travel into lost work. 0 means never expires. "
            "A destructive confirmation auto-REJECTS on expiry; an ordinary one keeps waiting.",
        ),
    )
    lease_ttl_secs: int = field(
        default=900,
        metadata=_meta(
            "Task Claim Lifetime",
            "How long a session's exclusive claim on a task lasts before another may take it. "
            "Deliberately short: a worker that needs longer renews, which proves it is alive, "
            "whereas a long lease only delays discovering that it is not. Capped at one hour.",
        ),
    )
    default_quiet_windows: str = field(
        default="",
        metadata=_meta(
            "Default Quiet Hours",
            "A quiet window applied to new automations that do not set their own, as "
            "`HH:MM-HH:MM` (e.g. `22:00-08:00`). Empty means no default — an automation you "
            "created deliberately should run when you told it to, so this only fills a gap you "
            "left. A window may wrap midnight. Per-trigger settings always win.",
        ),
    )
    duty_gate_default: str = field(
        default="",
        metadata=_meta(
            "Default Duty Gate",
            "The is-the-user-on-duty check applied to new automations that name none. Empty "
            "means no gate. `manual` is the built-in on/off toggle; apps can supply others (a "
            "calendar, for instance). The gate always fails OPEN — if it cannot answer, the "
            "automation still fires, so a broken calendar app can never silence everything.",
        ),
    )
    workspace_default_mode: str = field(
        default="scratch",
        metadata=_meta(
            "Default Workspace Mode",
            "Where a run works when its template declares no `workspace.mode`: `scratch` (a "
            "per-run directory), `worktree` (a git worktree of the project's workspace) or "
            "`in_place` (the real tree, no isolation). A template's own declaration always "
            "wins. `scratch` is the default because being wrong about isolation should cost a "
            "copy, not the original — and `in_place` is deliberately never the default, since "
            "that is the mode in which a destructive step runs against real state.",
        ),
    )
    workspace_teardown_on_expiry: bool = field(
        default=True,
        metadata=_meta(
            "Run Teardown Before Deletion",
            "Run a workspace's declared `teardown` command before its directory is deleted by "
            "retention or an explicit delete. On, because teardown's whole job is to stop "
            "services and sync work out while the directory still exists — turning it off "
            "leaves a `docker compose` up after the run that started it is gone.",
        ),
    )

    def lane_caps(self) -> dict[str, int]:
        caps = {
            lane: getattr(self, f"max_concurrent_{lane}_nodes")
            for lane in ("llm", "io")
        }
        return {**caps, "compute": 64}

    def model_tiers(self) -> dict[str, str]:
        return {
            tier: getattr(self, f"model_tier_{tier}")
            for tier in ("reasoning", "standard", "fast")
        }

    def __post_init__(self) -> None:
        floors = {
            "default_node_timeout_total_secs": 0,
            "default_node_timeout_stall_secs": 0,
        }
        for key, floor in floors.items():
            if getattr(self, key) < floor:
                object.__setattr__(self, key, floor)
        for tier, fallback in {
            "reasoning": "reasoning",
            "standard": "orchestration",
            "fast": "background",
        }.items():
            key = f"model_tier_{tier}"
            if not str(getattr(self, key, "") or "").strip():
                object.__setattr__(self, key, fallback)


def resolve_memory_store_config(top_level_memory: dict, store_overrides: dict) -> dict:
    eligible = {
        key: value
        for key, value in store_overrides.items()
        if key != "description" and value != "" and value is not None
    }
    return {**top_level_memory, **eligible}


@dataclass
class ResolvedBindings:
    """Resolved bindings for a session, from the selected Agent Definition.

    Carries the working dir, memory store, provider agent, AND the agent's
    behavioral fields (system_prompt/tools/skills/approval_mode) so the runtime
    honors what the Agents UI edits.
    """

    workspace_dir: Path
    memory_store_name: str
    effective_memory_config: dict
    provider_agent: str
    acp_mode: str = ""
    system_prompt: str = ""
    tools: list = field(default_factory=list)
    skills: list = field(default_factory=list)
    approval_mode: str = ""
    triggers: list = field(default_factory=list)
    provider: str = ""


@dataclass
class InboxConfig:
    """Inbox — reads your messages, drafts replies, presents for approval."""

    enabled: bool = field(
        default=False,
        metadata=_meta("Enabled", "Enable Inbox background polling."),
    )
    user_id: str = field(
        default="",
        metadata=_meta(
            "User ID", "Your user ID on the message source (set during setup)."
        ),
    )
    watched_channels: list[str] = field(
        default_factory=list,
        metadata=_meta("Watched Channels", "Channel IDs to monitor."),
    )
    poll_interval_seconds: int = field(
        default=60,
        metadata=_meta("Poll Interval", "Seconds between polls."),
    )
    style_rules: list[str] = field(
        default_factory=list,
        metadata=_meta(
            "Style Rules", "Initial communication style rules for drafting."
        ),
    )
    test_mode: bool = field(
        default=False,
        metadata=_meta("Test Mode", "Include own messages in inbox (for testing)."),
    )
    engagement_ranking_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Engagement Ranking",
            "Rank the inbox by how much you engage with each channel/sender (favorites, opens, "
            "replies boost; dismisses lower) on top of recency. Off = pure newest-first.",
        ),
    )
    engagement_half_life_days: float = field(
        default=0.0,
        metadata=_meta(
            "Engagement Half-life (days)",
            "How fast an engagement boost fades (0 = the default ~6.6 days). Lower = more reactive "
            "to recent behavior; higher = longer memory.",
        ),
    )


@dataclass
class ProjectionRuleConfig:
    """A user-taught tool-output projection rule (TokenJuice, OP6 + §2.3). Output whose
    head matches ``match_regex`` is projected with ``strategy`` (a builtin content type:
    log/diff/json/test/csv/code) — or, when any op field is set (head/tail/keep/skip/
    count), shaped by the declarative ops interpreter instead. Pure data; no user code
    runs."""

    name: str = field(
        default="",
        metadata=_meta("Rule Name", "A short label for this projection rule."),
    )
    match_regex: str = field(
        default="",
        metadata=_meta(
            "Match Regex",
            "Regex matched against the start of a tool's output; a match selects this rule's strategy.",  # noqa: E501
        ),
    )
    strategy: str = field(
        default="log",
        metadata=_meta(
            "Strategy", "The builtin projector to apply (log/diff/json/test/csv/code)."
        ),
    )
    head: int = field(
        default=0,
        metadata=_meta(
            "Keep Head Lines",
            "Keep the first N lines (0 = off). Op — overrides the strategy projector.",
        ),  # noqa: E501
    )
    tail: int = field(
        default=0,
        metadata=_meta(
            "Keep Tail Lines",
            "Keep the last N lines (0 = off). Op — overrides the strategy projector.",
        ),  # noqa: E501
    )
    keep: str = field(
        default="",
        metadata=_meta(
            "Keep Lines Matching", "Keep only lines matching this regex (empty = off)."
        ),
    )
    skip: str = field(
        default="",
        metadata=_meta(
            "Skip Lines Matching", "Drop lines matching this regex (empty = off)."
        ),
    )
    count: str = field(
        default="",
        metadata=_meta(
            "Fold Lines Matching",
            "Fold lines matching this regex into one 'N elided' note (empty = off).",
        ),
    )


@dataclass
class DurabilityConfig:
    """Scheduled backup + retention + drills (DURABILITY-AND-SYNC §3)."""

    auto_backup: bool = field(
        default=True,
        metadata=_meta(
            "Automatic backups",
            "Take a nightly snapshot and an hourly incremental export in the "
            "background, so losing work never depends on remembering to run a "
            "backup. Off means backups only happen when you run them by hand.",
        ),
    )
    keep_daily: int = field(
        default=14,
        metadata=_meta(
            "Keep daily snapshots",
            "How many days of nightly snapshots to retain before thinning to "
            "weeklies.",
        ),
    )
    keep_weekly: int = field(
        default=8,
        metadata=_meta(
            "Keep weekly snapshots", "How many weeks to keep one snapshot each."
        ),
    )
    keep_monthly: int = field(
        default=12,
        metadata=_meta(
            "Keep monthly snapshots", "How many months to keep one snapshot each."
        ),
    )
    restore_drills: bool = field(
        default=True,
        metadata=_meta(
            "Monthly restore drill",
            "Once a month, restore the newest snapshot into a temporary directory "
            "and verify it — a backup nobody has restored is a hope, not a backup. "
            "Never touches live data; reports pass or fail.",
        ),
    )
    time_travel: bool = field(
        default=True,
        metadata=_meta(
            "Workspace time travel",
            "Keep a local, minute-by-minute history of the things you and the "
            "assistant edit — configuration, skills, prompts, project context and "
            "the memory notes — so a bad edit is an undo rather than a restore. "
            "The history stays on this machine and is never synced or exported. "
            "Secrets are excluded from it entirely.",
        ),
    )
    sync_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Sync between machines",
            "Keep this Gideon in sync with your other machines through a shared "
            "remote (a git repo or a synced folder). Off by default; turning it on also "
            "requires choosing a sync transport. Sync never overwrites — it merges, and "
            "your local data is always kept.",
        ),
    )
    sync_transport: str = field(
        default="",
        metadata=_meta(
            "Sync transport",
            "Which installed sync transport to use (e.g. git-sync, dir-sync, rsync-sync, "
            "s3-sync). Empty means no transport is chosen yet, so sync stays idle even "
            "if enabled.",
        ),
    )
    sync_stale_after_secs: int = field(
        default=900,
        metadata=_meta(
            "Sync staleness window (seconds)",
            "How long to trust a recent remote check before pulling again. A short "
            "window syncs sooner at the cost of more remote polls; the default (15 "
            "minutes) balances freshness against chattiness.",
        ),
    )
    sync_encrypt: str = field(
        default="auto",
        metadata=_meta(
            "Encrypt synced data",
            "Encrypt everything before it leaves this machine, so a shared bucket or "
            "folder holds unreadable data without your passphrase. 'auto' picks the "
            "right default per transport — on for cloud storage and shared folders, "
            "off for a private git repo, where a readable history is the whole point. "
            "'on' and 'off' override that. Encryption needs a passphrase saved in the "
            "credential store; without one, sync stops rather than sending plain data.",
        ),
    )


@dataclass
class AgentsRoutingConfig:
    """Agent routing (AGENT-ROUTING) — suggest-first specialist routing. Deterministic
    classification (keyword + embedding, no LLM); a non-blocking chip proposes, the
    user consents. Silent auto-routing is explicitly out of scope."""

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "Agent routing suggestions",
            "When a message in a default-agent chat fits an installed specialist, "
            "show a one-click 'route to <agent>?' chip. Off = never suggested.",
        ),
    )
    min_confidence: float = field(
        default=0.62,
        metadata=_meta(
            "Routing confidence",
            "Minimum embedding-match confidence before a routing chip appears.",
        ),
    )
    cooldown_hours: float = field(
        default=24.0,
        metadata=_meta(
            "Routing dismiss cooldown (hours)",
            "After dismissing a suggestion for an agent, suppress it for this long "
            "(three cumulative dismissals mute the agent until you re-enable it).",
        ),
    )


@dataclass
class ToolsConfig:
    """Tool-output handling config. Today: user-teachable projection rules that extend
    the builtin content-type dispatch for large tool outputs (TokenJuice, OP6)."""

    projection_rules: list[ProjectionRuleConfig] = field(
        default_factory=list,
        metadata=_meta(
            "Projection Rules",
            "User-taught rules mapping a tool-output content marker (regex) to a "
            "builtin projection strategy (log/diff/json/test/csv), so a large output "
            "the sniffer would blunt-cut as generic keeps its salient slice instead. "
            "Consulted before the heuristic sniff; a bad regex is skipped.",
        ),
    )
    bg_compress_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Background compression",
            "Continuously compress old, idle conversation history in the background "
            "(topic-segmented, attention-weighted) so long sessions stay fast. Every "
            "dropped span is archived first (fully recoverable) and the summary names "
            "its archive. Incognito/temporary chats are never touched.",
        ),
    )
    bg_compress_idle_days: float = field(
        default=7.0,
        metadata=_meta(
            "Background compression idle window",
            "Only compress sessions untouched for at least this many days (at rest — "
            "an active session is never compressed).",
        ),
    )
    groups_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Tool groups",
            "Partition tools into named groups (one per tool provider) that the "
            "agent activates on demand, so unused groups don't spend context on "
            "their schemas. Every tool stays callable by name and searchable via "
            "tool_search — this saves context, it does not restrict capability. "
            "Interactive chat keeps every group active; background/loop/subagent "
            "runs start focused (see the per-surface defaults).",
        ),
    )
    group_defaults: dict[str, list[str]] = field(
        default_factory=dict,
        metadata=_meta(
            "Tool groups per surface",
            'Which tool groups start active per surface, e.g. {"background": '
            '["core", "memory"]}. Keys are session axes (background, loops, '
            'orchestration, chat); "*" means all groups. A surface with no entry '
            "keeps every group active. Overrides the built-in defaults.",
        ),
    )


@dataclass
class CheckpointsConfig:
    """Turn-bound file checkpointing bounds (EXECUTION-ISOLATION §6).

    The caps on ``turn_checkpoints``' per-session store: how many turns of pre-edit
    backups to keep, how many megabytes of file bodies, and how big a single body may be
    before it is recorded manifest-only. Guard-class note: these are BOUNDS, not a safety
    control — the secrecy floor (which files are never copied at all) is a code-level
    tuple in ``turn_checkpoints.NEVER_CAPTURE_GLOBS`` with no config field, deliberately,
    so no config edit can widen what the store is allowed to hold."""

    enabled: bool = field(
        default=True,
        metadata=_meta(
            "File Checkpoints",
            "Back up a file's current bytes before an agent's first write to it in a turn, "
            "so /rewind-to-turn can restore it. Filesystem-only: it never rewinds the "
            "conversation. Off means a wrong edit is unrecoverable.",
        ),
    )
    max_mb: int = field(
        default=200,
        metadata=_meta(
            "Checkpoint Store Cap (MB)",
            "Maximum megabytes of backed-up file bodies kept per session. When a new backup "
            "would exceed this, the oldest turns are pruned until it fits. 0 disables the "
            "byte cap (the turn cap still applies).",
        ),
    )
    max_turns: int = field(
        default=50,
        metadata=_meta(
            "Checkpoint Turns Kept",
            "How many recent turns of backups to keep per session. Older turns are pruned, "
            "which makes rewinding past them impossible — the preview says so.",
        ),
    )
    max_file_mb: int = field(
        default=8,
        metadata=_meta(
            "Checkpoint Max File (MB)",
            "A single file larger than this is recorded in the turn manifest but its bytes "
            "are not copied, so a rewind reports it as 'not captured' instead of restoring "
            "it. Keeps one large binary write from consuming the whole store cap. 0 "
            "disables the per-file limit.",
        ),
    )


@dataclass
class VoiceConfig:
    """Hands-free voice-loop knobs (MULTIMODAL-IO §4.5).

    Guard-class note: the four booleans are convenience features, not safety
    guards — plain defaults, no fail-safe parsing. Turning one off degrades the
    voice loop's comfort (more echo, code read aloud), never its safety, so a
    config typo must not be second-guessed here.
    """

    confirmation_phrases: list[str] = field(
        default_factory=lambda: list(DEFAULT_CONFIRMATION_PHRASES),
        metadata=_meta(
            "Confirmation Phrases",
            "In hands-free mode a dictated transcript accumulates and is only sent "
            "once one of these phrases ends what you just said, so a half-finished "
            "thought never becomes an executed instruction. Push-to-talk and typed "
            "input ignore this entirely.",
        ),
    )
    exit_phrases: list[str] = field(
        default_factory=lambda: list(DEFAULT_EXIT_PHRASES),
        metadata=_meta(
            "Exit Phrases",
            "Saying one of these in hands-free mode clears the accumulated "
            "transcript without sending it.",
        ),
    )
    echo_filter_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Echo Filter",
            "Drop a transcription that shares three consecutive words with what the "
            "assistant just spoke — the speaker bleeding back into the microphone. "
            "Applies only to hands-free requests; the dashboard shows the drop "
            "instead of looking deaf.",
        ),
    )
    duplex_mute_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Mute While Speaking",
            "Suspend the microphone and discard queued audio while a spoken reply "
            "plays. This is what kills most echo; the filter above is the backstop.",
        ),
    )
    clean_for_speech_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Clean Text Before Speaking",
            "Strip code blocks, reduce URLs to their domain and paths to their "
            "filename, and drop CLI flags before synthesis. The chat transcript "
            "always keeps the full text — only the audio is cleaned.",
        ),
    )
    push_to_talk_chord: str = field(
        default=DEFAULT_PUSH_TO_TALK_CHORD,
        metadata=_meta(
            "Push-to-Talk Shortcut",
            "The global shortcut the desktop app binds for push-to-talk: press to "
            "start capturing the microphone, press again to stop and transcribe into "
            "the composer. Needs at least one modifier — a bare key would be taken "
            "from every other app on the machine. The desktop shell is what actually "
            "binds it, so an unusable or already-taken chord is refused there with a "
            "reason rather than failing silently. Ignored in a browser tab, which has "
            "no global shortcuts.",
        ),
    )
    voice_disclaimer_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Voice-Origin Disclaimer",
            "Append a one-line note to a dictated message telling the model the text "
            "came from speech recognition and may be misheard, so it self-corrects on "
            "garbled homophones instead of confidently misreading them.",
        ),
    )


@dataclass
class UpdatesConfig:
    """Release-based update + release-tracking contract (RELEASE-UPDATE-MECHANISM RUM-1).

    The single block the CLI, container, desktop and the Settings > Updates screen read.
    RUM-1 is only the config surface + the legacy backfill; the resolver, the check
    kill-switch, the retirement of pull-from-main and the per-kind apply are later RUM
    atoms that CONSUME these fields.

    Legacy backfill (applied in ``AppConfig.load()``, idempotent — a clean break under the
    pre-1.0 banner, NOT a migration file): a home written before this block existed carries
    the old ``auto_update`` bool and ``dashboard.update_dev_mode`` bool. On load, when the
    ``updates`` block does not itself declare a field, ``auto_update=true`` maps to
    ``auto="staged"`` with ``channel="stable"`` (an existing auto-updating git user stops
    riding raw ``main`` and starts riding stable release tags), ``auto_update=false`` maps to
    ``auto="off"``, and ``dashboard.update_dev_mode=true`` maps to ``channel="nightly"``. An
    explicit ``updates`` field always wins over the legacy source.
    """

    channel: Literal["stable", "beta", "nightly"] = field(
        default="stable",
        metadata=_meta(
            "Update Channel",
            "Which release line this install tracks, chosen by how much churn you can "
            "absorb rather than by version number. 'stable' (default) follows the newest "
            "non-prerelease release — what almost everyone runs. 'beta' follows the newest "
            "release including release candidates, for the next minor early. 'nightly' is "
            "git checkouts only and the ONLY channel that ever tracks the current branch "
            "instead of a release tag — never a default, and it requires a clean tree.",
            enum=["stable", "beta", "nightly"],
        ),
    )
    pin: str = field(
        default="",
        metadata=_meta(
            "Version Pin",
            "Stay on an exact version (e.g. '0.2.1') or a version line, overriding the "
            "channel: 'update available' and any apply respect the pin. Empty (the default) "
            "means follow the channel. This is the 'stay on 0.2.x' and rollback story — "
            "artifacts are immutable and every version is kept.",
        ),
    )
    auto: Literal["off", "staged"] = field(
        default="off",
        metadata=_meta(
            "Automatic Updates",
            "'off' (default) is notify-only: an available update raises a notification and "
            "is NEVER applied unattended. 'staged' applies at the next safe point — it holds "
            "while a session or subagent is in flight and lands only on the resolved "
            "channel/pin release tag, never on raw main.",
            enum=["off", "staged"],
        ),
    )
    check_enabled: bool = field(
        default=True,
        metadata=_meta(
            "Check for Updates",
            "Whether the updater checks for a new release on a schedule. When off, the "
            "updater makes ZERO outbound calls to GitHub — the privacy/egress kill switch. "
            "On (the default), it checks every 'Check Interval Hours'.",
        ),
    )
    check_interval_hours: int = field(
        default=12,
        metadata=_meta(
            "Check Interval Hours",
            "How often (in hours) to check for a new release when checking is enabled "
            "(1-168). Ignored entirely when 'Check for Updates' is off.",
        ),
    )
    last_version: str = field(
        default="",
        metadata=_meta(
            "Last Running Version",
            "The version this install last ran, persisted so a rollback can offer "
            "'Roll back to v<last_version>'. Maintained by the updater; empty until the "
            "first recorded run.",
        ),
    )


class ConfigPreserveError(RuntimeError):
    """`AppConfig.save()` could not read the existing config, so it refused to write.

    Raised INSTEAD of silently dropping the `providers` / `use_cases` / `slack` blocks that live
    outside `to_dict()`. A caller seeing this should surface it: the user's stored provider
    credentials are intact on disk, and the save simply did not happen. Retrying once the file is
    readable is the correct recovery — writing anyway is what destroyed them before.
    """


@dataclass
class AppConfig:
    agent: AgentConfig = field(
        default_factory=AgentConfig,
        metadata=_meta("Agent", "Agent runtime configuration."),
    )
    sandbox: SandboxConfig = field(
        default_factory=SandboxConfig,
        metadata=_meta(
            "Sandbox", "Resource ceilings for agent-influenced child processes."
        ),
    )
    checkpoints: CheckpointsConfig = field(
        default_factory=CheckpointsConfig,
        metadata=_meta("File Checkpoints", "Bounds on turn-bound file checkpointing."),
    )
    session: SessionConfig = field(
        default_factory=SessionConfig,
        metadata=_meta("Session", "Session management settings."),
    )
    loops: LoopsConfig = field(
        default_factory=LoopsConfig,
        metadata=_meta("Autonomous", "Autonomous goal loop settings."),
    )
    memory: MemoryConfig = field(
        default_factory=MemoryConfig,
        metadata=_meta("Memory", "Memory and embedding configuration."),
    )
    skills: SkillsConfig = field(
        default_factory=SkillsConfig,
        metadata=_meta("Skills", "Skill loading and matching configuration."),
    )
    learning: LearningConfig = field(
        default_factory=LearningConfig,
        metadata=_meta("Learning", "Per-turn self-improvement review configuration."),
    )
    knowledge: KnowledgeConfig = field(
        default_factory=KnowledgeConfig,
        metadata=_meta("Knowledge", "Knowledge-store write semantics and expiry."),
    )
    workflows: WorkflowsConfig = field(
        default_factory=WorkflowsConfig,
        metadata=_meta("Workflows", "Workflow SOP surfacing configuration."),
    )
    security: SecurityConfig = field(
        default_factory=SecurityConfig,
        metadata=_meta("Security", "Shell-command security controls."),
    )
    auth: AuthConfigSection = field(
        default_factory=AuthConfigSection,
        metadata=_meta(
            "Login", "Owner login — an additional front door, off by default."
        ),
    )
    routing: RoutingConfig = field(
        default_factory=RoutingConfig,
        metadata=_meta(
            "Model Routing",
            "Which of your bound models handles which kind of request.",
        ),
    )
    guardrails: GuardrailsConfig = field(
        default_factory=GuardrailsConfig,
        metadata=_meta("Guardrails", "Autonomy safety floor — budgets, breaker, scan."),
    )
    resilience: ResilienceConfig = field(
        default_factory=ResilienceConfig,
        metadata=_meta(
            "Resilience", "Doctor health surface + no-model degraded indicator."
        ),
    )
    voice: VoiceConfig = field(
        default_factory=VoiceConfig,
        metadata=_meta(
            "Voice", "Hands-free voice loop — gating, echo filter, spoken text."
        ),
    )
    inbox: InboxConfig = field(
        default_factory=InboxConfig,
        metadata=_meta("Inbox", "Reads messages, drafts replies."),
    )
    tools: ToolsConfig = field(
        default_factory=ToolsConfig,
        metadata=_meta(
            "Tools", "Tool-output handling — user-teachable projection rules."
        ),
    )
    feedback: FeedbackConfig = field(
        default_factory=FeedbackConfig,
        metadata=_meta(
            "Feedback", "👍/👎 capture on AI judgments + accuracy thresholds."
        ),
    )
    external_access: ExternalAccessConfig = field(
        default_factory=ExternalAccessConfig,
        metadata=_meta(
            "External Access", "The shared inbound access seam (off by default)."
        ),
    )
    agents_routing: AgentsRoutingConfig = field(
        default_factory=AgentsRoutingConfig,
        metadata=_meta("Agent Routing", "Suggest-first specialist routing."),
    )
    planning: PlanningConfig = field(
        default_factory=PlanningConfig,
        metadata=_meta("Planning", "Planner entry surfaces — the watched scratchpad."),
    )

    dashboard: DashboardConfig = field(
        default_factory=DashboardConfig,
        metadata=_meta("Dashboard", "Dashboard UI settings."),
    )
    legibility: LegibilityConfig = field(
        default_factory=LegibilityConfig,
        metadata=_meta(
            "Legibility",
            "Platform-legibility features — Discover tips + context adapters.",
        ),
    )
    ambient: AmbientConfig = field(
        default_factory=AmbientConfig,
        metadata=_meta(
            "Ambient", "Composable home + generative UI + tray companion settings."
        ),
    )
    companion: CompanionConfig = field(
        default_factory=CompanionConfig,
        metadata=_meta(
            "Companion apps",
            "LAN discovery + instance name for native companion clients.",
        ),
    )
    browse: BrowseConfig = field(
        default_factory=BrowseConfig,
        metadata=_meta(
            "Browsing", "Which browser an autonomous browse task is allowed to drive."
        ),
    )
    mobile: MobileConfig = field(
        default_factory=MobileConfig,
        metadata=_meta(
            "Mobile push", "Which transport carries a content-free push to the phone."
        ),
    )
    local_models: LocalModelsConfig = field(
        default_factory=LocalModelsConfig,
        metadata=_meta(
            "Local models",
            "Memory pressure, sidecar restart, model fit, and Hugging Face authentication.",
        ),
    )
    sources: SourcesConfig = field(
        default_factory=SourcesConfig,
        metadata=_meta(
            "Watched sources", "Poll engine for watched feeds, pages and directories."
        ),
    )
    packs: PacksConfig = field(
        default_factory=PacksConfig,
        metadata=_meta(
            "Packs", "Pack import + skill-catalog + connector-catalog settings."
        ),
    )
    apps: AppsConfig = field(
        default_factory=AppsConfig,
        metadata=_meta(
            "Apps", "App Store settings that are not per-app (default sources)."
        ),
    )
    hooks: dict = field(
        default_factory=dict,
        metadata=_meta("Hooks", "Script hook definitions keyed by hook ID."),
    )
    observe_max_messages: int = field(
        default=200,
        metadata=_meta(
            "Observe Max Messages", "Max messages per observe-mode channel."
        ),
    )
    observe_ttl_hours: float = field(
        default=168.0,
        metadata=_meta("Observe TTL Hours", "Hours to keep observe history."),
    )
    agents: dict[str, AgentProfile] = field(
        default_factory=dict,
        metadata=_meta("Agents", "Named Gideon agent definitions."),
    )
    default_agent: str = field(
        default="",
        metadata=_meta(
            "Default Agent", "Active Gideon agent name from the agents section."
        ),
    )
    memory_stores: dict[str, MemoryStoreConfig] = field(
        default_factory=dict,
        metadata=_meta("Memory Stores", "Named memory store definitions."),
    )
    auto_update: bool = field(
        default=True,
        metadata=_meta(
            "Auto Update",
            "Automatically apply updates when a new version is found "
            "(update checks always run; this gates the unattended "
            "pull + rebuild + restart).",
        ),
    )
    updates: "UpdatesConfig" = field(
        default_factory=lambda: UpdatesConfig(),
        metadata=_meta(
            "Updates",
            "Release-based update + release-tracking: channel, version pin, opt-in staged "
            "apply, the check kill-switch and interval, and the last-running version for "
            "rollback (RELEASE-UPDATE-MECHANISM).",
        ),
    )
    timezone: str = field(
        default="",
        metadata=_meta(
            "Timezone",
            "IANA timezone name (e.g. 'America/Los_Angeles'). "
            "Used to display cron schedules in local time.",
        ),
    )
    snapshot_dir: str = field(
        default="",
        metadata=_meta(
            "Snapshot Directory",
            "Directory for gideon snapshot output. "
            "Defaults to ~/.gideon/snapshots if empty.",
        ),
    )
    durability: "DurabilityConfig" = field(
        default_factory=lambda: DurabilityConfig(),
        metadata=_meta(
            "Durability", "Scheduled backups, retention, and restore drills."
        ),
    )
    evals: "EvalsConfig" = field(
        default_factory=lambda: EvalsConfig(),
        metadata=_meta(
            "Evals",
            "The offline eval substrate — studies, ablation, retrieval/judge benchmarks.",
        ),
    )
    proactive: "ProactiveConfig" = field(
        default_factory=lambda: ProactiveConfig(),
        metadata=_meta(
            "Proactive",
            "The scheduled triage digest, its auto-execution bounds, and the decision "
            "journal's default review horizon.",
        ),
    )

    @classmethod
    def load(cls) -> "AppConfig":
        """Load config from ~/.gideon/config.json, falling back to defaults.

        A PURE READ. Pending migrations are applied to the returned object in memory and
        nothing is written, so importing a module that reads config cannot rewrite the
        user's file. ``config.migrations.load_and_persist_migrations()`` is the counterpart
        that persists them, and only the gateway's boot path calls it.
        """
        return cls.load_with_migration_state()[0]

    @classmethod
    def load_with_migration_state(cls) -> tuple["AppConfig", bool]:
        from gideon.core.config.decoding import decode_configuration
        from gideon.core.config.document import read_configuration

        values = read_configuration(config_path(), logger)
        if values is None:
            return cls(memory_stores={"default": MemoryStoreConfig()}), False
        _validate_config_data(values)
        configuration = decode_configuration(values, cls)
        try:
            from gideon.core.config.migrations import apply_config_migrations

            changed = apply_config_migrations(configuration)
        except Exception as failure:
            logger.warning("Config migration failed: %s", failure)
            changed = False
        return configuration, changed

    def to_dict(self) -> dict:
        from gideon.core.config.document import configuration_values

        return configuration_values(self)

    def save(self) -> None:
        from gideon.core.config.document import write_configuration

        write_configuration(config_path(), self.to_dict(), ConfigPreserveError)

    def load_credentials(self) -> dict[str, str]:
        from gideon.core.config.credentials import (
            _dotenv_credentials,
            _keychain_credentials,
        )

        layers = (
            _dotenv_credentials(),
            _keychain_credentials(),
            {key: os.environ[key] for key in _CREDENTIAL_KEYS if os.environ.get(key)},
        )
        credentials = {key: value for layer in layers for key, value in layer.items()}
        for key in credentials.keys() - os.environ.keys():
            if credentials[key]:
                os.environ[key] = credentials[key]
        return credentials

    def create_provider_factory(self) -> Callable:
        from gideon.extensions.providers import provider_bridge

        return provider_bridge.create_provider_factory("chat")


def resolve_agent_bindings(
    config: AppConfig, agent_name: str | None = None
) -> ResolvedBindings:
    from gideon.core.config.resolution import AgentBindingPlan

    plan = AgentBindingPlan(
        config,
        workspace_root,
        _compose_voice,
        resolve_memory_store_config,
        ResolvedBindings,
        logger,
    )
    return plan.resolve(agent_name)


def resolve_session_workspace(
    config: "AppConfig", agent_name: str | None, current: str = ""
) -> str:
    profile = (config.agents or {}).get(agent_name) if agent_name else None
    declared = str(getattr(profile, "default_dir", "") or "").strip() if profile else ""
    selected = declared or str(current or "").strip()
    return selected or str(resolve_agent_bindings(config, agent_name).workspace_dir)
