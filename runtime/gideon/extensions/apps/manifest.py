"""App manifest — static metadata for Gideon apps.

An app manifest (``app.json``) declares an app's identity, resources, and
requirements without executing any app code.  Gideon reads it during
install to register agents, skills, crons, UI pages, and backend config.

Design follows the same pattern as :class:`backend.plugins.manifest.PluginManifest`
(dataclass + ``from_dict`` / ``to_dict`` / ``validate`` / round-trip) but with
app-specific fields.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([+-]|$)")
ROUTE_OP_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ICON_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def version_tuple(v: str) -> tuple[int, ...]:
    """Parse an app semver string to a numeric tuple for comparison (best-effort).

    Manifests are validated against ``SEMVER_RE`` (``MAJOR.MINOR.PATCH`` with an optional
    ``+build``/``-pre`` suffix), so the core is the dotted release. The pre-release/build
    suffix is dropped (SemVer pre-release ordering is out of scope for "is a newer release
    available"), and a leading ``v`` is tolerated. A value that can't be parsed sorts as
    ``(0,)`` so a malformed version never falsely reads as an available update.

    This is the ONE app-version comparator (``apps.catalog`` reuses it) — the version
    field and ``SEMVER_RE`` both live here, so the comparator does too, rather than
    inverting the apps→dashboard layering to borrow the self-updater's tag comparator.
    """
    core = (v or "").strip()
    core = core[1:] if core[:1] == "v" else core
    core = core.split("+", 1)[0].split("-", 1)[0]
    try:
        return tuple(int(x) for x in core.split("."))
    except (ValueError, AttributeError):
        return (0,)


def strict_version_tuple(v: str) -> tuple[int, ...] | None:
    """``v`` as a comparable tuple, or ``None`` when it is not a valid app semver.

    :func:`version_tuple` is deliberately best-effort — it sorts junk as ``(0,)`` so a
    malformed version never reads as an available update. That collapse is exactly wrong
    for a *floor*: a floor of ``(0,)`` is satisfied by every core, so the same helper
    would turn a typo into a silently-disabled gate. This variant keeps the parse and the
    verdict separate: ``None`` means "unmeasurable", and the caller decides which way
    that falls. Shape is ``SEMVER_RE`` (``MAJOR.MINOR.PATCH`` + optional ``-pre``/
    ``+build``), with the same leading-``v`` tolerance and the same suffix drop, so there
    is still ONE notion of an app version string in this module.
    """
    core = (v or "").strip()
    core = core[1:] if core[:1] == "v" else core
    if not SEMVER_RE.match(core):
        return None
    return version_tuple(core)


CORE_COMPAT_OK = "ok"
CORE_COMPAT_INVALID = "invalid"
CORE_COMPAT_UNKNOWN_HOST = "unknown_host_version"
CORE_COMPAT_INCOMPATIBLE = "incompatible"


def host_core_version() -> str:
    """The running core's version — what a declared floor is compared against.

    Imported lazily so this module keeps its stdlib-only import surface (it is parsed
    by the CLI scaffolder and by catalog scans that must not pull the world in)."""
    from gideon import __version__

    return str(__version__)


@dataclass(frozen=True)
class CoreCompatibility:
    """Whether the running core satisfies an app's declared ``minGideonVersion``."""

    state: str = CORE_COMPAT_OK
    required: str = ""
    host: str = ""

    @property
    def admits(self) -> bool:
        """Whether an app carrying this verdict may be installed / enabled / started."""
        return self.state != CORE_COMPAT_INCOMPATIBLE

    @property
    def reason(self) -> str:
        """A user-facing sentence, or ``""`` for :data:`CORE_COMPAT_OK`.

        Names BOTH versions in every non-``ok`` state, and — where the user can act —
        says what to do next. Prefixed with the app name by the caller, which is the
        layer that knows it."""
        if self.state == CORE_COMPAT_INCOMPATIBLE:
            return (
                f"requires Gideon {self.required} or newer, but this core is "
                f"{self.host}. Upgrade the core — run `gideon update` — then "
                f"try again."
            )
        if self.state == CORE_COMPAT_INVALID:
            return (
                f"declares minGideonVersion {self.required!r}, which is not a "
                f"MAJOR.MINOR.PATCH version, so its core-version floor cannot be "
                f"checked against this core ({self.host}) and is being ignored. Fix the "
                f"value in app.json to restore the gate."
            )
        if self.state == CORE_COMPAT_UNKNOWN_HOST:
            return (
                f"requires Gideon {self.required} or newer; this core reports "
                f"{self.host!r}, which is not a comparable version, so the floor cannot "
                f"be checked and is being allowed."
            )
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "required": self.required,
            "host": self.host,
            "reason": self.reason,
        }


def check_core_version(required: str, host: str | None = None) -> CoreCompatibility:
    """Evaluate a declared ``minGideonVersion`` against the running core.

    THE one owner of this decision. Every path that puts an app into effect
    (install, update, enable, gateway boot) routes here rather than re-deriving the
    comparison, so there is a single place where "which way does an unparseable value
    fall" is answered. ``host`` is injectable for tests; production passes ``None``.
    """
    host_version = host_core_version() if host is None else str(host)
    declared = (required or "").strip()
    if not declared:
        return CoreCompatibility(CORE_COMPAT_OK, "", host_version)
    want = strict_version_tuple(declared)
    if want is None:
        return CoreCompatibility(CORE_COMPAT_INVALID, declared, host_version)
    have = strict_version_tuple(host_version)
    if have is None:
        return CoreCompatibility(CORE_COMPAT_UNKNOWN_HOST, declared, host_version)
    if have < want:
        return CoreCompatibility(CORE_COMPAT_INCOMPATIBLE, declared, host_version)
    return CoreCompatibility(CORE_COMPAT_OK, declared, host_version)


@dataclass
class CronEntry:
    """A scheduled agent job declared by an app."""

    name: str = ""
    every: int = 0
    cron_expr: str = ""
    agent: str = ""
    message: str = ""
    agent_sequence: list[str] = field(
        default_factory=list
    )  # ordered list of agents to run
    env: dict[str, str] = field(default_factory=dict)
    persistent_session: bool = True
    silent: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name}
        if self.every:
            d["every"] = self.every
        if self.cron_expr:
            d["cron_expr"] = self.cron_expr
        if self.agent:
            d["agent"] = self.agent
        if self.message:
            d["message"] = self.message
        if self.agent_sequence:
            d["agent_sequence"] = self.agent_sequence
        if self.env:
            d["env"] = self.env
        if not self.persistent_session:
            d["persistent_session"] = False
        if self.silent:
            d["silent"] = True
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronEntry":
        return cls(
            name=str(data.get("name", "")),
            every=int(data.get("every", 0)),
            cron_expr=str(data.get("cron_expr", "")),
            agent=str(data.get("agent", "")),
            message=str(data.get("message", "")),
            agent_sequence=[str(a) for a in data.get("agent_sequence", [])],
            env={str(k): str(v) for k, v in data.get("env", {}).items()},
            persistent_session=bool(data.get("persistent_session", True)),
            silent=bool(data.get("silent", False)),
        )


@dataclass
class UIPage:
    """A frontend page contributed by an app."""

    route: str = ""
    label: str = ""
    icon: str = ""
    iconUrl: str = ""  # custom icon image path relative to ui/ dir  # noqa: N815
    entryPoint: str = ""  # path to JS bundle relative to app root  # noqa: N815
    mountFunction: str = "mount"  # exported function name  # noqa: N815

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "route": self.route,
            "label": self.label,
        }
        if self.icon:
            d["icon"] = self.icon
        if self.iconUrl:
            d["iconUrl"] = self.iconUrl
        if self.entryPoint:
            d["entryPoint"] = self.entryPoint
        if self.mountFunction != "mount":
            d["mountFunction"] = self.mountFunction
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UIPage":
        return cls(
            route=str(data.get("route", "")),
            label=str(data.get("label", "")),
            icon=str(data.get("icon", "")),
            iconUrl=str(data.get("iconUrl", "")),  # noqa: N815
            entryPoint=str(data.get("entryPoint", "")),  # noqa: N815
            mountFunction=str(data.get("mountFunction", "mount")),  # noqa: N815
        )


@dataclass
class UISidebar:
    """Sidebar placement config for app pages."""

    section: str = "Apps"
    order: int = 10

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.section != "Apps":
            d["section"] = self.section
        if self.order != 10:
            d["order"] = self.order
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UISidebar":
        return cls(
            section=str(data.get("section", "Apps")),
            order=int(data.get("order", 10)),
        )


@dataclass
class UIConfig:
    """Frontend configuration for an app."""

    entry: str = ""
    pages: list[UIPage] = field(default_factory=list)
    sidebar: UISidebar = field(default_factory=UISidebar)
    components: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.entry:
            d["entry"] = self.entry
        if self.pages:
            d["pages"] = [p.to_dict() for p in self.pages]
        if self.components:
            d["components"] = self.components
        sidebar_d = self.sidebar.to_dict()
        if sidebar_d:
            d["sidebar"] = sidebar_d
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UIConfig":
        pages = [
            UIPage.from_dict(p) for p in data.get("pages", []) if isinstance(p, dict)
        ]
        sidebar_raw = data.get("sidebar", {})
        sidebar = (
            UISidebar.from_dict(sidebar_raw)
            if isinstance(sidebar_raw, dict)
            else UISidebar()
        )
        return cls(
            entry=str(data.get("entry", "")),
            pages=pages,
            sidebar=sidebar,
            components=str(data.get("components", "")),
        )


@dataclass
class AppSkill:
    """One SKILL.md skill directory an app ships and OWNS (§4.1).

    Declared as ``{path: "skills/my-skill/"}`` (dir path relative to the app root,
    containing a ``SKILL.md``). On enable / startup discovery the dir is installed
    into the user skills tree through the supply-chain chokepoint
    (:meth:`SkillsRegistry.install_scanned` → quarantine → ``scan_dir`` at the app's
    trust tier → ``.gideon-lock.json``) — an app skill never bypasses the gate just
    because it arrived inside an app. Idempotent + non-clobbering; removed on
    disable/uninstall keyed by the app's own declaration. See apps.skill_seed.
    """

    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSkill":
        return cls(path=str(data.get("path", "")))


@dataclass
class RouteEntry:
    """One agent-callable backend route an app declares in its manifest.

    Declared statically in ``backend.routes[]`` so the agent-callable surface is
    readable WITHOUT executing app code (the manifest module's design rule). Each
    entry names a stable ``op`` (the tool suffix ``app_<name>_<op>``), the HTTP
    ``method`` + ``path`` on the app backend, a human ``summary``, and optional
    JSON-schema-ish ``params`` (query/path) / ``body`` hints. ``agentCallable``
    (default True) gates whether the route is exposed as an agent tool + through
    ``call-app-route`` — a declared-but-not-callable route documents the surface
    without surfacing it. See :class:`~gideon.integrations.tool_providers` AppRoutesToolProvider.
    """

    op: str = ""
    method: str = "GET"
    path: str = ""
    summary: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    agentCallable: bool = True  # expose as agent tool + call-app-route  # noqa: N815

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"op": self.op, "method": self.method, "path": self.path}
        if self.summary:
            d["summary"] = self.summary
        if self.params:
            d["params"] = self.params
        if self.body:
            d["body"] = self.body
        if not self.agentCallable:
            d["agentCallable"] = False
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RouteEntry":
        return cls(
            op=str(data.get("op", "")),
            method=str(data.get("method", "GET")).upper() or "GET",
            path=str(data.get("path", "")),
            summary=str(data.get("summary", "")),
            params=(
                dict(data.get("params", {}))
                if isinstance(data.get("params"), dict)
                else {}
            ),
            body=(
                dict(data.get("body", {})) if isinstance(data.get("body"), dict) else {}
            ),
            agentCallable=bool(data.get("agentCallable", True)),  # noqa: N815
        )


@dataclass
class BackendConfig:
    """Backend process configuration for an app."""

    entryPoint: str = ""  # e.g. backend/app.py or dist/main.js  # noqa: N815
    port: str = "auto"
    healthCheck: str = "/health"  # health check endpoint path  # noqa: N815
    type: str = ""
    sandbox: str = ""
    routes: list[RouteEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.entryPoint:
            d["entryPoint"] = self.entryPoint
        if self.port != "auto":
            d["port"] = self.port
        if self.healthCheck != "/health":
            d["healthCheck"] = self.healthCheck
        if self.type:
            d["type"] = self.type
        if self.sandbox:
            d["sandbox"] = self.sandbox
        if self.routes:
            d["routes"] = [r.to_dict() for r in self.routes]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackendConfig":
        return cls(
            entryPoint=str(data.get("entryPoint", "")),  # noqa: N815
            port=str(data.get("port", "auto")),
            healthCheck=str(data.get("healthCheck", "/health")),  # noqa: N815
            type=str(data.get("type", "")),
            sandbox=str(data.get("sandbox", "")),
            routes=[
                RouteEntry.from_dict(r)
                for r in data.get("routes", [])
                if isinstance(r, dict)
            ],
        )


@dataclass
class ProposalKind:
    """One proposal kind an app declares it may emit (INU-7, ``permissions.proposals[]``).

    ``kind_suffix`` is namespaced under the app at registration
    (``("app:<name>", "proposal:<kind_suffix>")``), so two apps declaring ``draft`` never
    collide and the user's notification rules address each app's kind separately. It is
    slug-shaped on purpose: a suffix carrying ``/`` or whitespace would break the
    ``<source>/<kind>`` rules-store key.
    """

    kind_suffix: str
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind_suffix": self.kind_suffix, "label": self.label}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProposalKind":
        suffix = str(data.get("kind_suffix") or "")
        return cls(kind_suffix=suffix, label=str(data.get("label") or suffix))

    def is_valid(self) -> bool:
        return bool(_PROPOSAL_SUFFIX_RE.match(self.kind_suffix))


_PROPOSAL_SUFFIX_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


@dataclass
class Permissions:
    """Declared permissions for an app."""

    api: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    mcpTools: list[str] = field(default_factory=list)  # noqa: N815
    storage: bool = False
    network: bool = False
    network_declared: bool = False
    memory: str = ""
    cron: bool = False
    agent: bool = False
    appMessaging: list[str] = field(default_factory=list)  # noqa: N815
    storageShared: bool = False  # noqa: N815
    storageRead: list[str] = field(default_factory=list)  # noqa: N815
    desktop: list[str] = field(default_factory=list)
    proposals: list["ProposalKind"] = field(default_factory=list)
    backgroundTasks: bool = False  # noqa: N815
    eventSubscriptions: list[str] = field(default_factory=list)  # noqa: N815

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.api:
            d["api"] = self.api
        if self.events:
            d["events"] = self.events
        if self.mcpTools:
            d["mcpTools"] = self.mcpTools
        if self.storage:
            d["storage"] = True
        if self.network or self.network_declared:
            d["network"] = bool(self.network)
        if self.memory:
            d["memory"] = self.memory
        if self.cron:
            d["cron"] = True
        if self.agent:
            d["agent"] = True
        if self.appMessaging:
            d["appMessaging"] = self.appMessaging
        if self.storageShared:
            d["storageShared"] = True
        if self.storageRead:
            d["storageRead"] = self.storageRead
        if self.desktop:
            d["desktop"] = self.desktop
        if self.proposals:
            d["proposals"] = [p.to_dict() for p in self.proposals]
        if self.backgroundTasks:
            d["backgroundTasks"] = True
        if self.eventSubscriptions:
            d["eventSubscriptions"] = self.eventSubscriptions
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Permissions":
        return cls(
            api=[str(p) for p in data.get("api", []) if p],
            events=[str(e) for e in data.get("events", []) if e],
            mcpTools=[str(t) for t in data.get("mcpTools", []) if t],  # noqa: N815
            storage=bool(data.get("storage", False)),
            network=bool(data.get("network", False)),
            network_declared="network" in data,
            memory=str(data.get("memory", "")),
            cron=bool(data.get("cron", False)),
            agent=bool(data.get("agent", False)),
            appMessaging=[
                str(t) for t in data.get("appMessaging", []) if t
            ],  # noqa: N815
            storageShared=bool(data.get("storageShared", False)),  # noqa: N815
            storageRead=[
                str(t) for t in data.get("storageRead", []) if t
            ],  # noqa: N815
            desktop=[str(c) for c in data.get("desktop", []) if c],
            proposals=[
                ProposalKind.from_dict(p)
                for p in data.get("proposals", [])
                if isinstance(p, dict)
            ],
            backgroundTasks=bool(data.get("backgroundTasks", False)),  # noqa: N815
            eventSubscriptions=[  # noqa: N815
                str(e) for e in data.get("eventSubscriptions", []) if e
            ],
        )

    def proposal_kind(self, kind_suffix: str) -> "ProposalKind | None":
        """The declared kind for *kind_suffix*, or None — the 403 check reads THIS."""
        for entry in self.proposals:
            if entry.kind_suffix == kind_suffix:
                return entry
        return None


@dataclass
class SetupConfig:
    """Installation and setup configuration for an app."""

    onInstall: str = ""  # shell command run after first install  # noqa: N815
    onUpdate: str = (
        ""  # shell command run after update (new code in place)  # noqa: N815
    )
    onUninstall: str = ""  # shell command run before removing app files  # noqa: N815
    onEnable: str = ""  # shell command run when app is enabled  # noqa: N815
    onDisable: str = ""  # shell command run when app is disabled  # noqa: N815
    onEnableTimeout: int = 30  # seconds; configurable per-app  # noqa: N815
    onDisableTimeout: int = 30  # seconds; configurable per-app  # noqa: N815
    configSchema: dict[str, Any] = field(default_factory=dict)  # noqa: N815

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.onInstall:
            d["onInstall"] = self.onInstall
        if self.onUpdate:
            d["onUpdate"] = self.onUpdate
        if self.onUninstall:
            d["onUninstall"] = self.onUninstall
        if self.onEnable:
            d["onEnable"] = self.onEnable
        if self.onDisable:
            d["onDisable"] = self.onDisable
        if self.onEnableTimeout != 30:
            d["onEnableTimeout"] = self.onEnableTimeout
        if self.onDisableTimeout != 30:
            d["onDisableTimeout"] = self.onDisableTimeout
        if self.configSchema:
            d["configSchema"] = self.configSchema
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SetupConfig":
        return cls(
            onInstall=str(data.get("onInstall", "")),  # noqa: N815
            onUpdate=str(data.get("onUpdate", "")),  # noqa: N815
            onUninstall=str(data.get("onUninstall", "")),  # noqa: N815
            onEnable=str(data.get("onEnable", "")),  # noqa: N815
            onDisable=str(data.get("onDisable", "")),  # noqa: N815
            onEnableTimeout=int(data.get("onEnableTimeout", 30)),  # noqa: N815
            onDisableTimeout=int(data.get("onDisableTimeout", 30)),  # noqa: N815
            configSchema=dict(data.get("configSchema", {})),  # noqa: N815
        )


@dataclass
class CliConfig:
    """App-contributed CLI seams (residue #3, #4 — Plan 32).

    An app may hook into the two core CLI commands without living in core:

    - ``setup`` — a ``"module:function"`` entry point (relative to the app dir)
      run during ``gideon setup`` after the core steps. The function
      receives a :class:`gideon.sdk.cli.SetupContext` and runs its own
      interactive step (e.g. collecting provider tokens).
    - ``doctor`` — a ``"module:function"`` entry point run during
      ``gideon doctor``; it returns a ``list[DoctorLine]`` that the doctor
      renderer prints as a per-app section.

    Both are optional and default to empty (the app contributes nothing).
    Static data only — the module path is stored, never imported, at parse time.
    """

    setup: str = ""
    doctor: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.setup:
            d["setup"] = self.setup
        if self.doctor:
            d["doctor"] = self.doctor
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CliConfig":
        return cls(
            setup=str(data.get("setup", "")),
            doctor=str(data.get("doctor", "")),
        )


@dataclass
class MarketplaceDependencies:
    """Marketplace-managed dependencies (MCP servers, skills, agents)."""

    mcp: list[Any] = field(default_factory=list)
    skills: list[Any] = field(default_factory=list)
    agents: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.mcp:
            d["mcp"] = self.mcp
        if self.skills:
            d["skills"] = self.skills
        if self.agents:
            d["agents"] = self.agents
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarketplaceDependencies":
        return cls(
            mcp=list(data.get("mcp", [])),
            skills=list(data.get("skills", [])),
            agents=list(data.get("agents", [])),
        )


@dataclass
class Dependencies:
    """External dependencies that Gideon should resolve during install.

    ``managedBy`` controls the default installation strategy:
      - ``"gateway"``: Gideon runs the skills CLI for each dependency
      - ``"app"``: Gideon only checks existence, does not install

    Individual entries can override via object format:
    ``{"id": "some-mcp", "managedBy": "app"}``

    ``pythonDependencies`` are pip requirement specifiers (e.g.
    ``"faster-whisper>=1.0"``) the app needs at runtime. Core ships LEAN — heavy
    ML/provider libs (sentence-transformers, faster-whisper, boto3, …) are NOT
    core deps; the app that needs one declares it here and the installer pip-installs
    it into the shared core venv at install/update time. A newly-introduced dep
    requires a gateway RESTART to import (the running process already imported its
    modules) — surfaced to the user via the install result's ``restart_required``.
    """

    managedBy: str = "gateway"  # noqa: N815
    marketplace: "MarketplaceDependencies" = field(
        default_factory=MarketplaceDependencies
    )
    commands: list[str] = field(default_factory=list)
    pythonDependencies: list[str] = field(default_factory=list)  # noqa: N815

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.managedBy != "gateway":
            d["managedBy"] = self.managedBy
        mkt_d = self.marketplace.to_dict()
        if mkt_d:
            d["marketplace"] = mkt_d
        if self.commands:
            d["commands"] = self.commands
        if self.pythonDependencies:
            d["pythonDependencies"] = self.pythonDependencies
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Dependencies":
        mkt_raw = data.get("marketplace", {})
        marketplace = (
            MarketplaceDependencies.from_dict(mkt_raw)
            if isinstance(mkt_raw, dict)
            else MarketplaceDependencies()
        )
        return cls(
            managedBy=str(data.get("managedBy", "gateway")),  # noqa: N815
            marketplace=marketplace,
            commands=[str(c) for c in data.get("commands", [])],
            pythonDependencies=[
                str(p) for p in data.get("pythonDependencies", [])
            ],  # noqa: N815
        )


@dataclass
class ClientInstallConfig:
    """Instructions for installing an app on the user's local machine.

    Used when Gideon runs on a remote host and the app requires a
    specific local platform (e.g. macOS for Electron apps).
    """

    shell: str = ""
    postInstall: str = (
        ""  # command to run after install (e.g. "open ~/Applications/MyApp.app")  # noqa: N815
    )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.shell:
            d["shell"] = self.shell
        if self.postInstall:
            d["postInstall"] = self.postInstall
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClientInstallConfig":
        return cls(
            shell=str(data.get("shell", "")),
            postInstall=str(data.get("postInstall", "")),  # noqa: N815
        )


@dataclass
class PlatformConfig:
    """Platform requirements and install mode for an app.

    ``os`` declares which platforms the app can run on.
    ``installMode`` controls how the App Store handles installation:

    - ``"server"`` (default): Gideon clones + installs on the server.
    - ``"client"``: Must be installed on the user's local machine.
      When Gideon is on an incompatible platform, the App Store shows
      copy-paste terminal instructions instead of running the install.
    """

    os: list[str] = field(default_factory=lambda: ["macos", "linux"])
    arch: list[str] = field(default_factory=list)
    installMode: str = "server"  # "server" | "client"  # noqa: N815
    clientInstall: ClientInstallConfig = field(
        default_factory=ClientInstallConfig
    )  # noqa: N815

    _OS_TO_PLATFORM = {"macos": "darwin", "linux": "linux"}
    _PLATFORM_TO_OS = {"darwin": "macos", "linux": "linux"}

    def supports_platform(self, sys_platform: str) -> bool:
        """Check if this platform config supports the given sys.platform value."""
        return sys_platform in {self._OS_TO_PLATFORM.get(o, o) for o in self.os}

    @staticmethod
    def current_os() -> str:
        """Return the user-friendly OS name for the current platform."""
        import sys

        return PlatformConfig._PLATFORM_TO_OS.get(sys.platform, sys.platform)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.os != ["macos", "linux"]:
            d["os"] = self.os
        if self.arch:
            d["arch"] = self.arch
        if self.installMode != "server":
            d["installMode"] = self.installMode
        ci = self.clientInstall.to_dict()
        if ci:
            d["clientInstall"] = ci
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlatformConfig":
        ci_raw = data.get("clientInstall", {})
        ci = (
            ClientInstallConfig.from_dict(ci_raw)
            if isinstance(ci_raw, dict)
            else ClientInstallConfig()
        )
        return cls(
            os=[str(o) for o in data.get("os", ["macos", "linux"])],
            arch=[str(a) for a in data.get("arch", [])],
            installMode=str(data.get("installMode", "server")),  # noqa: N815
            clientInstall=ci,  # noqa: N815
        )


PACK_FETCH_METHODS = frozenset({"GET", "HEAD"})

PACK_ARG_TYPES = frozenset({"string", "integer", "boolean"})

PACK_SECRET_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "x-api-key", "api-key"}
)

PACK_PLACEHOLDER_RE = re.compile(r"\{\{([^{}]*)\}\}")
_PACK_ARG_REF_RE = re.compile(r"^args\.([a-z][a-z0-9_]*)$")
_PACK_SECRET_REF_RE = re.compile(r"^secret:([A-Za-z_][A-Za-z0-9_]*)$")


@dataclass
class PackSourceEntry:
    """One parse-only source a connector pack contributes (WATCHED-SOURCES §7.1).

    ``script`` parses; it does not fetch. ``fetchSpec`` is a URL template plus method and
    headers that the ENGINE renders and requests through ``net.fetch``, piping the body to
    ``script`` on stdin. ``argsSchema`` declares the per-source variables a user supplies
    (a repo name, a subreddit) that the template interpolates.

    The split is the whole security story: the pack's contribution is a parser, and a parser
    holds no network capability. See ``knowledge_providers/pack_parse.py`` for what enforces
    that at runtime.
    """

    name: str = ""
    script: str = ""
    displayName: str = ""  # noqa: N815
    description: str = ""
    fetchSpec: dict[str, Any] = field(default_factory=dict)  # noqa: N815
    argsSchema: dict[str, Any] = field(default_factory=dict)  # noqa: N815

    def declared_args(self) -> list[str]:
        """The arg names ``argsSchema`` declares (the only names a template may reference)."""
        return (
            [str(k) for k in self.argsSchema]
            if isinstance(self.argsSchema, dict)
            else []
        )

    def _validate_placeholders(self, raw: str, where: str) -> list[str]:
        """Every ``{{...}}`` in ``raw`` must be a declared arg or a secret reference."""
        errors: list[str] = []
        declared = set(self.declared_args())
        for token in PACK_PLACEHOLDER_RE.findall(raw):
            token = token.strip()
            arg = _PACK_ARG_REF_RE.match(token)
            if arg:
                if arg.group(1) not in declared:
                    errors.append(
                        f"source {self.name!r} {where} references undeclared arg "
                        f"{arg.group(1)!r} (declare it in argsSchema)"
                    )
                continue
            if _PACK_SECRET_REF_RE.match(token):
                if where == "fetchSpec.url":
                    errors.append(
                        f"source {self.name!r} must not put a secret in fetchSpec.url "
                        f"({{{{{token}}}}}); use a header"
                    )
                continue
            errors.append(
                f"source {self.name!r} {where} has unknown placeholder {{{{{token}}}}} "
                f"(only {{{{args.<name>}}}} and {{{{secret:<KEY>}}}} exist)"
            )
        return errors

    def validate(self) -> list[str]:
        """Errors in this entry (empty means valid). Static — no app code is executed."""
        errors: list[str] = []
        if not self.name:
            errors.append("source entry missing required field: name")
        elif not KEBAB_RE.match(self.name):
            errors.append(f"source name must be kebab-case, got: {self.name!r}")
        if not self.script:
            errors.append(f"source {self.name!r} missing required field: script")
        else:
            if ".." in self.script:
                errors.append(f"source script contains path traversal: {self.script!r}")
            if self.script.startswith("/"):
                errors.append(
                    f"source script must be relative to the app dir: {self.script!r}"
                )
            if not self.script.endswith(".py"):
                errors.append(f"source script must be a .py file: {self.script!r}")
        if not isinstance(self.argsSchema, dict):
            errors.append(f"source {self.name!r} argsSchema must be an object")
        else:
            for arg_name, decl in self.argsSchema.items():
                if not isinstance(decl, dict):
                    errors.append(
                        f"source {self.name!r} argsSchema.{arg_name} must be an object"
                    )
                    continue
                kind = str(decl.get("type", "string") or "string")
                if kind not in PACK_ARG_TYPES:
                    errors.append(
                        f"source {self.name!r} argsSchema.{arg_name}.type must be one of "
                        f"{sorted(PACK_ARG_TYPES)}, got: {kind!r}"
                    )
        if not isinstance(self.fetchSpec, dict):
            errors.append(f"source {self.name!r} fetchSpec must be an object")
            return errors
        url = str(self.fetchSpec.get("url", "") or "")
        if not url:
            errors.append(f"source {self.name!r} missing required field: fetchSpec.url")
        elif not url.lower().startswith(("http://", "https://")):
            errors.append(
                f"source {self.name!r} fetchSpec.url must be http(s): {url[:80]!r}"
            )
        else:
            errors.extend(self._validate_placeholders(url, "fetchSpec.url"))
        method = str(self.fetchSpec.get("method", "GET") or "GET").upper()
        if method not in PACK_FETCH_METHODS:
            errors.append(
                f"source {self.name!r} fetchSpec.method must be one of "
                f"{sorted(PACK_FETCH_METHODS)}, got: {method!r}"
            )
        headers = self.fetchSpec.get("headers", {})
        if not isinstance(headers, dict):
            errors.append(f"source {self.name!r} fetchSpec.headers must be an object")
        else:
            for header, value in headers.items():
                text = str(value)
                errors.extend(
                    self._validate_placeholders(text, f"fetchSpec.headers.{header}")
                )
                if (
                    str(header).lower() in PACK_SECRET_HEADERS
                    and "{{secret:" not in text
                ):
                    errors.append(
                        f"source {self.name!r} fetchSpec.headers.{header} must reference a "
                        f"{{{{secret:KEY}}}} rather than an inline value — a manifest ships "
                        f"to a Store, so a literal there is a published credential"
                    )
        unknown = sorted(
            set(self.fetchSpec) - {"url", "method", "headers", "accept", "description"}
        )
        if unknown:
            errors.append(
                f"source {self.name!r} fetchSpec has unknown key(s) {unknown}"
            )
        return errors

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "script": self.script}
        if self.displayName:
            d["displayName"] = self.displayName
        if self.description:
            d["description"] = self.description
        if self.fetchSpec:
            d["fetchSpec"] = self.fetchSpec
        if self.argsSchema:
            d["argsSchema"] = self.argsSchema
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PackSourceEntry":
        return cls(
            name=str(data.get("name", "")),
            script=str(data.get("script", "")),
            displayName=str(data.get("displayName", "")),  # noqa: N815
            description=str(data.get("description", "")),
            fetchSpec=(  # noqa: N815
                dict(data["fetchSpec"])
                if isinstance(data.get("fetchSpec"), dict)
                else {}
            ),
            argsSchema=(  # noqa: N815
                dict(data["argsSchema"])
                if isinstance(data.get("argsSchema"), dict)
                else {}
            ),
        )


PROVIDER_TYPES = frozenset(
    {
        "context_engine",
        "model",
        "agent",
        "task",
        "channel",
        "inbox",
        "skills",
        "knowledge",
        "memory",
        "notification",
        "tool",
        "workflow",
        "search",
        "action",
        "prompt",
        "duty_gate",
        "sync",
        "sandbox",
        "trigger_source",
        "trigger",
        "vector_store",
    }
)

_HOOK_OR_ENTRYPOINT_RE = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*:[a-zA-Z_][a-zA-Z0-9_]*$"
)

# LOCAL-MODEL-MANAGER-V2 §3.1 — where a provider's heavy work runs. This is a field on
# the EXISTING ``model`` type, deliberately NOT a new provider type: PROVIDER_TYPES above
# and the ``_TypeHandler`` set are untouched, so ``test_manifest_types_match_handlers``
# stays green by construction and every registration seam is unchanged.
EXECUTION_IN_PROCESS = "in-process"
EXECUTION_SIDECAR = "sidecar"
EXECUTION_MODES = frozenset({EXECUTION_IN_PROCESS, EXECUTION_SIDECAR})


@dataclass
class AutonomyConfig:
    """An app-contributed action's declared autonomy bounds (AUTONOMY-GUARDRAILS §5.2).

    ``floor`` is the rung the action starts at; ``ceiling`` is the rung it can never pass
    however much track record accrues. Both name a rung from
    ``guardrails.autonomy.RUNGS`` (``draft_only`` → ``one_tap`` → ``auto_with_undo`` →
    ``autonomous``). Empty means UNDECLARED, and an undeclared action behaves exactly as it
    did before this block existed — the block is purely additive, and a manifest without
    one round-trips byte-identically.

    Two things this deliberately does NOT let an app say:

    * **``leaves_machine``.** Core derives it from the app's own ``permissions.network``
      declaration. An app that could self-certify "my action stays on this machine" would
      be self-certifying its way to the top of the ladder.
    * **``autonomous`` for an action that reaches the network.** ``ceiling`` is clamped to
      ``clamp_untrusted_ceiling``'s bound at registration, LOUDLY (a log line and a SEL
      row naming both the declared and the granted ceiling). A manifest's claim has had no
      in-tree review, and a silent downgrade is a recorded finding in this codebase.
    """

    floor: str = ""
    ceiling: str = ""

    def validate(self) -> list[str]:
        from gideon.security.guardrails.autonomy import RUNGS

        errors: list[str] = []
        for label, value in (("floor", self.floor), ("ceiling", self.ceiling)):
            if value and value not in RUNGS:
                errors.append(
                    f"provider.autonomy.{label} must be one of {list(RUNGS)}, got: {value!r}"
                )
        if (
            self.floor
            and self.ceiling
            and self.floor in RUNGS
            and self.ceiling in RUNGS
        ):
            if RUNGS.index(self.ceiling) < RUNGS.index(self.floor):
                errors.append(
                    f"provider.autonomy.ceiling {self.ceiling!r} is below its floor {self.floor!r}"
                )
        return errors

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.floor:
            d["floor"] = self.floor
        if self.ceiling:
            d["ceiling"] = self.ceiling
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutonomyConfig":
        return cls(
            floor=str(data.get("floor", "")), ceiling=str(data.get("ceiling", ""))
        )


@dataclass
class ProviderConfig:
    """Declares that this extension provides a pluggable provider implementation.

    ``type`` identifies the entity class (model, agent, task, etc.).
    ``implementation`` is a Python entry point in ``module.path:factory_fn``
    format, resolved relative to the extension's directory.  The factory
    receives the extension's current config dict and returns a provider instance.
    ``settingsSchema`` is a JSON Schema (Draft-07 + x-meta) describing
    user-configurable settings for this provider.
    """

    type: str = ""
    implementation: str = ""
    multiInstance: bool = False  # noqa: N815
    settingsSchema: dict[str, Any] = field(default_factory=dict)  # noqa: N815
    capabilities: list[str] = field(default_factory=list)
    providerType: str = ""  # noqa: N815
    # Optional entity sub-grouping within a provider type. Hook providers, for
    # example, are all ``type: "hook"`` but each acts on a distinct entity
    # (task, agent, comms, …). The Settings UI sub-groups cards of one type by
    # this value so "Create Task Hook" sits under a "Task Hook Provider" group.
    # Empty → the UI treats the provider as belonging to its type's default group.
    entity: str = ""
    autonomy: AutonomyConfig = field(default_factory=AutonomyConfig)
    execution: str = EXECUTION_IN_PROCESS

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.execution not in EXECUTION_MODES:
            errors.append(
                f"provider.execution must be one of {sorted(EXECUTION_MODES)}, "
                f"got: {self.execution!r}"
            )
        if not self.type:
            errors.append("provider.type is required")
        elif self.type not in PROVIDER_TYPES:
            errors.append(
                f"provider.type must be one of {sorted(PROVIDER_TYPES)}, got: {self.type!r}"
            )
        if not self.implementation:
            errors.append("provider.implementation is required")
        elif not _HOOK_OR_ENTRYPOINT_RE.match(self.implementation):
            errors.append(
                f"provider.implementation must be 'module.path:factory_fn', "
                f"got: {self.implementation!r}"
            )
        errors.extend(self.autonomy.validate())
        return errors

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.type:
            d["type"] = self.type
        if self.implementation:
            d["implementation"] = self.implementation
        if self.multiInstance:
            d["multiInstance"] = True
        if self.settingsSchema:
            d["settingsSchema"] = self.settingsSchema
        if self.capabilities:
            d["capabilities"] = self.capabilities
        if self.entity:
            d["entity"] = self.entity
        if self.providerType:
            d["providerType"] = self.providerType
        autonomy_d = self.autonomy.to_dict()
        if autonomy_d:
            d["autonomy"] = autonomy_d
        if self.execution != EXECUTION_IN_PROCESS:
            d["execution"] = self.execution
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderConfig":
        autonomy_raw = data.get("autonomy", {})
        return cls(
            type=str(data.get("type", "")),
            implementation=str(data.get("implementation", "")),
            multiInstance=bool(data.get("multiInstance", False)),  # noqa: N815
            settingsSchema=dict(data.get("settingsSchema", {})),  # noqa: N815
            capabilities=[str(c) for c in data.get("capabilities", [])],
            entity=str(data.get("entity", "")),
            providerType=str(data.get("providerType", "")),  # noqa: N815
            autonomy=(
                AutonomyConfig.from_dict(autonomy_raw)
                if isinstance(autonomy_raw, dict)
                else AutonomyConfig()
            ),
            execution=str(
                data.get("execution", EXECUTION_IN_PROCESS) or EXECUTION_IN_PROCESS
            ),
        )


DESIGN_SYSTEM_LEVELS = frozenset({"v2", "legacy", "n/a"})

UI_CAPABILITIES = frozenset(
    {"shell-primitives", "generative-widget", "generative-component"}
)

QUALITY_AXES = ("tested", "designSystem", "a11y")


@dataclass
class QualityDeclaration:
    """An app's SELF-DECLARED quality bar — the Store's badge row (APE-4).

    Deliberately TRI-STATE per axis, because "absent" and "declared false" are
    different facts and a surface that renders them identically lies:

    * ``None``  — the app claims NOTHING on this axis. No badge. Not a pass, and
      NOT silently upgraded to ``False``: nobody said the app is untested, only
      that it didn't say.
    * ``False`` / ``"legacy"`` / ``"n/a"`` — the app declares it does NOT meet the
      bar. An honest miss: shown as a miss, never verified (there is no claim to
      falsify), never shown as a pass.
    * ``True`` / ``"v2"`` — a CLAIM, and therefore checkable. For a first-party app
      :mod:`gideon.extensions.apps.quality` demands the evidence in CI and fails the
      build when the evidence is absent or contradicts the claim.

    A claim with nothing to check is also a violation, not a free pass — see
    ``quality.verify_app``: declaring ``designSystem: "v2"`` with no frontend source
    or ``a11y: true`` with no UI would badge a check that never ran.
    """

    tested: bool | None = None
    designSystem: str | None = None  # noqa: N815 — "v2" | "legacy" | "n/a"
    a11y: bool | None = None

    def claims(self) -> tuple[str, ...]:
        """The axes this app actively CLAIMS to meet (the verifiable subset)."""
        out: list[str] = []
        if self.tested is True:
            out.append("tested")
        if self.designSystem == "v2":
            out.append("designSystem")
        if self.a11y is True:
            out.append("a11y")
        return tuple(out)

    def declared(self, axis: str) -> bool:
        """Whether ``axis`` was declared at all (either direction)."""
        return getattr(self, axis, None) is not None

    def to_dict(self) -> dict[str, Any]:
        """Emit only the axes that were DECLARED — an unclaimed axis stays absent
        on the wire, so no consumer can mistake silence for a ``False``."""
        d: dict[str, Any] = {}
        if self.tested is not None:
            d["tested"] = self.tested
        if self.designSystem is not None:
            d["designSystem"] = self.designSystem
        if self.a11y is not None:
            d["a11y"] = self.a11y
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QualityDeclaration":
        def tri(key: str) -> bool | None:
            if key not in data or data[key] is None:
                return None
            return bool(data[key])

        raw_ds = data.get("designSystem")
        return cls(
            tested=tri("tested"),
            designSystem=(None if raw_ds is None else str(raw_ds)),  # noqa: N815
            a11y=tri("a11y"),
        )


_KNOWN_FIELDS = frozenset(
    {
        "name",
        "version",
        "displayName",
        "description",
        "icon",
        "heroImage",
        "author",
        "license",
        "minGideonVersion",
        "prompts",
        "mcpServers",
        "crons",
        "ui",
        "backend",
        "permissions",
        "setup",
        "tags",
        "platform",
        "dependencies",
        "provider",
        "providers",
        "sources",
        "native",
        "cli",
        "loggerRoots",
        "skills",
        "quality",
        "uiCapabilities",
        "agents",
        "sops",
    }
)


@dataclass
class AppManifest:
    """Static metadata for a Gideon app — readable without executing app code.

    Parsed from ``app.json`` at the root of an app package.  Follows the same
    pattern as :class:`~backend.plugins.manifest.PluginManifest`: dataclass
    with ``validate`` / ``to_dict`` / ``from_dict`` / round-trip support.
    """

    name: str = ""
    version: str = ""
    displayName: str = ""  # human-readable name  # noqa: N815
    description: str = ""

    icon: str = ""
    heroImage: str = ""  # noqa: N815
    author: str = ""
    license: str = ""
    minGideonVersion: str = ""  # noqa: N815

    prompts: list[str] = field(default_factory=list)

    skills: list[AppSkill] = field(default_factory=list)

    mcpServers: dict[str, Any] = field(
        default_factory=dict
    )  # MCP server configs  # noqa: N815

    crons: list[CronEntry] = field(default_factory=list)

    ui: UIConfig = field(default_factory=UIConfig)

    backend: BackendConfig = field(default_factory=BackendConfig)

    permissions: Permissions = field(default_factory=Permissions)

    setup: SetupConfig = field(default_factory=SetupConfig)

    cli: CliConfig = field(default_factory=CliConfig)

    loggerRoots: list[str] = field(default_factory=list)  # noqa: N815

    dependencies: Dependencies = field(default_factory=Dependencies)

    platform: PlatformConfig = field(default_factory=PlatformConfig)

    provider: ProviderConfig | None = None
    providers: list[ProviderConfig] = field(default_factory=list)

    sources: list[PackSourceEntry] = field(default_factory=list)

    native: bool = False

    quality: QualityDeclaration | None = None

    uiCapabilities: list[str] = field(default_factory=list)  # noqa: N815

    tags: list[str] = field(default_factory=list)

    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        """Return list of validation errors (empty list means valid)."""
        errors: list[str] = []

        if not self.name:
            errors.append("missing required field: name")
        elif not KEBAB_RE.match(self.name):
            errors.append(
                f"name must be kebab-case (lowercase alphanumeric + hyphens), got: {self.name!r}"
            )

        if not self.version:
            errors.append("missing required field: version")
        elif not SEMVER_RE.match(self.version):
            errors.append(f"version must be semver (e.g. 1.0.0), got: {self.version!r}")

        if not self.displayName:
            errors.append("missing required field: displayName")

        if not self.description:
            errors.append("missing required field: description")

        hooks = self.extra.get("hooks", [])
        if not isinstance(hooks, list):
            errors.append("hooks must be an array")
        else:
            from gideon.engine.hooks import HOOK_EVENTS

            seen_hooks: set[str] = set()
            for hook in hooks:
                if not isinstance(hook, dict):
                    errors.append("hooks entries must be objects")
                    continue
                hook_name = hook.get("name")
                if not isinstance(hook_name, str) or not KEBAB_RE.fullmatch(hook_name):
                    errors.append(f"hook name must be kebab-case: {hook_name!r}")
                elif hook_name in seen_hooks:
                    errors.append(f"duplicate hook name: {hook_name!r}")
                else:
                    seen_hooks.add(hook_name)
                if hook.get("event") not in HOOK_EVENTS:
                    errors.append(f"hook {hook_name!r} has an unsupported event")
                if not isinstance(hook.get("provider"), str) or not hook.get("provider"):
                    errors.append(f"hook {hook_name!r} needs a provider")
                if not isinstance(hook.get("providerConfig", {}), dict):
                    errors.append(f"hook {hook_name!r} providerConfig must be an object")
                timeout = hook.get("timeout", 30)
                if type(timeout) is not int or not 1 <= timeout <= 300:
                    errors.append(f"hook {hook_name!r} timeout must be 1–300 seconds")

        if self.icon and not ICON_RE.fullmatch(self.icon):
            errors.append(f"icon must be a bare identifier, got: {self.icon!r}")

        for p in self.prompts:
            if ".." in str(p):
                errors.append(f"prompts path contains path traversal: {p!r}")

        for sk in self.skills:
            if ".." in str(sk.path):
                errors.append(f"skills path contains path traversal: {sk.path!r}")

        if self.ui.entry and ".." in self.ui.entry:
            errors.append(f"ui.entry contains path traversal: {self.ui.entry!r}")

        if self.ui.components and ".." in self.ui.components:
            errors.append(
                f"ui.components contains path traversal: {self.ui.components!r}"
            )
        if self.ui.components and "generative-component" not in self.uiCapabilities:
            errors.append(
                "ui.components requires the generative-component UI capability — declare it in "
                "uiCapabilities, or drop the components module"
            )

        for page in self.ui.pages:
            page_name = page.label or page.route or "<unnamed>"
            if not page.route:
                errors.append("ui page missing required field: route")
            if not page.label:
                errors.append("ui page missing required field: label")
            if page.icon and not ICON_RE.fullmatch(page.icon):
                errors.append(
                    f"ui page {page_name!r} icon must be a bare identifier, got: {page.icon!r}"
                )
            icon_url = page.iconUrl.replace("\\", "/")
            if page.iconUrl and (
                icon_url.startswith("/")
                or re.match(r"^[A-Za-z]:", icon_url)
                or ".." in icon_url.split("/")
            ):
                errors.append(
                    f"ui page {page_name!r} iconUrl contains path traversal: {page.iconUrl!r}"
                )
            if page.entryPoint and ".." in page.entryPoint:
                errors.append(
                    f"ui page entryPoint contains path traversal: {page.entryPoint!r}"
                )

        for cron in self.crons:
            if not cron.name:
                errors.append("cron entry missing required field: name")
            if not cron.every and not cron.cron_expr:
                errors.append(
                    f"cron entry {cron.name!r} must specify either 'every' or 'cron_expr'"
                )

        seen_suffixes: set[str] = set()
        for pk in self.permissions.proposals:
            if not pk.kind_suffix:
                errors.append(
                    "permissions.proposals entry missing required field: kind_suffix"
                )
            elif not pk.is_valid():
                errors.append(
                    f"proposal kind_suffix must be a slug (lowercase alphanumeric, "
                    f"'-' or '_'), got: {pk.kind_suffix!r}"
                )
            elif pk.kind_suffix in seen_suffixes:
                errors.append(f"duplicate proposal kind_suffix: {pk.kind_suffix!r}")
            else:
                seen_suffixes.add(pk.kind_suffix)

        seen_ops: set[str] = set()
        for r in self.backend.routes:
            if not r.op:
                errors.append("backend route missing required field: op")
            elif not ROUTE_OP_RE.match(r.op):
                errors.append(
                    f"backend route op must be a valid identifier "
                    f"(lowercase alphanumeric + underscores), got: {r.op!r}"
                )
            elif r.op in seen_ops:
                errors.append(f"backend route op declared more than once: {r.op!r}")
            else:
                seen_ops.add(r.op)
            if not r.path:
                errors.append(f"backend route {r.op!r} missing required field: path")
            elif not r.path.startswith("/"):
                errors.append(
                    f"backend route {r.op!r} path must start with '/': {r.path!r}"
                )
            if ".." in r.path:
                errors.append(f"backend route path contains path traversal: {r.path!r}")

        for prov in self.all_providers():
            errors.extend(prov.validate())

        errors.extend(self._validate_sources())

        if self.quality is not None and self.quality.designSystem is not None:
            if self.quality.designSystem not in DESIGN_SYSTEM_LEVELS:
                errors.append(
                    f"quality.designSystem must be one of "
                    f"{sorted(DESIGN_SYSTEM_LEVELS)}, got: {self.quality.designSystem!r}"
                )

        unknown_caps = [c for c in self.uiCapabilities if c not in UI_CAPABILITIES]
        if unknown_caps:
            errors.append(
                f"uiCapabilities entries must be one of {sorted(UI_CAPABILITIES)}, "
                f"got unknown: {sorted(set(unknown_caps))}"
            )
        if len(set(self.uiCapabilities)) != len(self.uiCapabilities):
            dupes = sorted(
                {c for c in self.uiCapabilities if self.uiCapabilities.count(c) > 1}
            )
            errors.append(f"uiCapabilities has duplicate entries: {dupes}")

        return errors

    def _validate_sources(self) -> list[str]:
        """Errors in the connector-pack ``sources`` block (WATCHED-SOURCES §7.1).

        Two cross-field rules make the kind COHERENT rather than merely well-formed, and both
        are install-time refusals because both failures are otherwise silent:

        * a ``sources`` block with no ``knowledge``/``source`` provider is a set of scripts
          nothing can ever drive — the declared-but-inert shape this codebase keeps finding;
        * a ``sources`` block without ``permissions.network`` makes the install-consent card
          read "Network access: not declared" for an app whose entire purpose is scheduled
          outbound fetching. ``network`` is disclosure and not containment — the card says so
          in as many words (``installConsent.tsx``, and app-platform.md §permissions) — but
          disclosure that reads the wrong way is worse than none, and the fetch happens
          BECAUSE the pack asked for it even though core is what performs it.
        """
        errors: list[str] = []
        if not self.sources:
            return errors
        seen: set[str] = set()
        for entry in self.sources:
            errors.extend(entry.validate())
            if entry.name:
                if entry.name in seen:
                    errors.append(f"duplicate source name: {entry.name!r}")
                seen.add(entry.name)
        has_source_provider = any(
            p.type == "knowledge" and "source" in p.capabilities
            for p in self.all_providers()
        )
        if not has_source_provider:
            errors.append(
                "sources[] requires a provider with type 'knowledge' and the 'source' "
                "capability — without one the scripts are declared and unreachable"
            )
        if not self.permissions.network:
            errors.append(
                "sources[] requires permissions.network: core performs the fetch, but it "
                "happens because this pack asked, and without the declaration the install "
                "card reads 'Network access: not declared'"
            )
        return errors

    def pack_source(self, name: str) -> "PackSourceEntry | None":
        """The declared source named ``name``, or None — what a WatchedSource spec resolves."""
        for entry in self.sources:
            if entry.name == name:
                return entry
        return None

    def validated_source_specs(self) -> tuple[PackSourceEntry, ...]:
        """The authoritative per-source specs, after applying manifest validation."""
        errors = self._validate_sources()
        if errors:
            raise ValueError("; ".join(errors))
        return tuple(self.sources)

    def all_providers(self) -> list[ProviderConfig]:
        """Every provider this app registers — the single ``provider`` (if any)
        followed by ``providers`` — so callers iterate one list regardless of how
        the manifest declared them. An app may register multiple providers of the
        same or different kinds."""
        out: list[ProviderConfig] = []
        if self.provider:
            out.append(self.provider)
        out.extend(self.providers)
        return out

    def core_compatibility(self, host: str | None = None) -> CoreCompatibility:
        """Whether the running core satisfies this app's ``minGideonVersion``.

        Never raises and never touches the filesystem, so a read surface (the Store
        card) and a write surface (install / update / enable / boot) can both ask.
        See :func:`check_core_version` for which way each unparseable case falls."""
        return check_core_version(self.minGideonVersion, host)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict, including extra fields."""
        d: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "displayName": self.displayName,
            "description": self.description,
        }
        if self.icon:
            d["icon"] = self.icon
        if self.heroImage:
            d["heroImage"] = self.heroImage
        if self.author:
            d["author"] = self.author
        if self.license:
            d["license"] = self.license
        if self.minGideonVersion:
            d["minGideonVersion"] = self.minGideonVersion
        if self.prompts:
            d["prompts"] = self.prompts
        if self.skills:
            d["skills"] = [s.to_dict() for s in self.skills]
        if self.mcpServers:
            d["mcpServers"] = self.mcpServers
        if self.crons:
            d["crons"] = [c.to_dict() for c in self.crons]
        ui_d = self.ui.to_dict()
        if ui_d:
            d["ui"] = ui_d
        backend_d = self.backend.to_dict()
        if backend_d:
            d["backend"] = backend_d
        perms_d = self.permissions.to_dict()
        if perms_d:
            d["permissions"] = perms_d
        setup_d = self.setup.to_dict()
        if setup_d:
            d["setup"] = setup_d
        cli_d = self.cli.to_dict()
        if cli_d:
            d["cli"] = cli_d
        if self.loggerRoots:
            d["loggerRoots"] = self.loggerRoots
        deps_d = self.dependencies.to_dict()
        if deps_d:
            d["dependencies"] = deps_d
        platform_d = self.platform.to_dict()
        if platform_d:
            d["platform"] = platform_d
        if self.provider:
            provider_d = self.provider.to_dict()
            if provider_d:
                d["provider"] = provider_d
        if self.providers:
            providers_d = [p.to_dict() for p in self.providers]
            providers_d = [p for p in providers_d if p]
            if providers_d:
                d["providers"] = providers_d
        if self.sources:
            d["sources"] = [s.to_dict() for s in self.sources]
        if self.quality is not None:
            quality_d = self.quality.to_dict()
            if quality_d:
                d["quality"] = quality_d
        if self.uiCapabilities:
            d["uiCapabilities"] = list(self.uiCapabilities)
        if self.native:
            d["native"] = True
        if self.tags:
            d["tags"] = self.tags
        d.update(self.extra)
        return d

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppManifest":
        """Parse from dict, preserving unknown fields in ``extra``."""
        extra = {k: v for k, v in data.items() if k not in _KNOWN_FIELDS}

        crons_raw = data.get("crons", [])
        crons = [CronEntry.from_dict(c) for c in crons_raw if isinstance(c, dict)]

        ui_raw = data.get("ui", {})
        ui = UIConfig.from_dict(ui_raw) if isinstance(ui_raw, dict) else UIConfig()

        backend_raw = data.get("backend", {})
        backend = (
            BackendConfig.from_dict(backend_raw)
            if isinstance(backend_raw, dict)
            else BackendConfig()
        )

        perms_raw = data.get("permissions", {})
        permissions = (
            Permissions.from_dict(perms_raw)
            if isinstance(perms_raw, dict)
            else Permissions()
        )

        setup_raw = data.get("setup", {})
        setup = (
            SetupConfig.from_dict(setup_raw)
            if isinstance(setup_raw, dict)
            else SetupConfig()
        )

        cli_raw = data.get("cli", {})
        cli = CliConfig.from_dict(cli_raw) if isinstance(cli_raw, dict) else CliConfig()

        deps_raw = data.get("dependencies", {})
        deps = (
            Dependencies.from_dict(deps_raw)
            if isinstance(deps_raw, dict)
            else Dependencies()
        )

        platform_raw = data.get("platform", {})
        platform_cfg = (
            PlatformConfig.from_dict(platform_raw)
            if isinstance(platform_raw, dict)
            else PlatformConfig()
        )

        provider_raw = data.get("provider")
        provider_cfg = (
            ProviderConfig.from_dict(provider_raw)
            if isinstance(provider_raw, dict)
            else None
        )

        providers_cfg = [
            ProviderConfig.from_dict(p)
            for p in data.get("providers", [])
            if isinstance(p, dict)
        ]

        quality_raw = data.get("quality")
        quality_cfg = (
            QualityDeclaration.from_dict(quality_raw)
            if isinstance(quality_raw, dict)
            else None
        )

        return cls(
            name=str(data.get("name", "")),
            version=str(data.get("version", "")),
            displayName=str(data.get("displayName", "")),  # noqa: N815
            description=str(data.get("description", "")),
            icon=str(data.get("icon", "")),
            heroImage=str(data.get("heroImage", "")),  # noqa: N815
            author=str(data.get("author", "")),
            license=str(data.get("license", "")),
            minGideonVersion=str(data.get("minGideonVersion", "")),  # noqa: N815
            prompts=[str(p) for p in data.get("prompts", []) if p],
            skills=[
                AppSkill.from_dict(s)
                for s in data.get("skills", [])
                if isinstance(s, dict) and s.get("path")
            ],
            mcpServers=dict(data.get("mcpServers", {})),  # noqa: N815
            crons=crons,
            ui=ui,
            backend=backend,
            permissions=permissions,
            setup=setup,
            cli=cli,
            loggerRoots=[str(r) for r in data.get("loggerRoots", []) if r],
            dependencies=deps,
            platform=platform_cfg,
            provider=provider_cfg,
            providers=providers_cfg,
            sources=[
                PackSourceEntry.from_dict(s)
                for s in data.get("sources", [])
                if isinstance(s, dict) and s.get("name")
            ],
            native=bool(data.get("native", False)),
            quality=quality_cfg,
            uiCapabilities=[
                str(c) for c in data.get("uiCapabilities", []) if c
            ],  # noqa: N815
            tags=[str(t) for t in data.get("tags", []) if t],
            extra=extra,
        )

    @classmethod
    def from_json_file(cls, path: Path) -> "AppManifest":
        """Parse from an ``app.json`` file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(
                f"app.json must be a JSON object, got {type(data).__name__}"
            )
        return cls.from_dict(data)
